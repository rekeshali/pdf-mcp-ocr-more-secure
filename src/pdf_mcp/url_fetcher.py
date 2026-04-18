"""URL fetching utilities for downloading PDFs from HTTPS sources.

The `_validate_url` method enforces the hardened URL floor adopted by this
fork: HTTPS-only schemes + an explicit non-removable SSRF block list checked
against every DNS-resolved IP. Mirrors the TypeScript implementation in the
sibling `pdf-reader-mcp-more-secure` fork at `src/utils/urlValidator.ts`.

Threat model note: there's a small TOCTOU gap between our DNS check here and
httpx's lookup at fetch time. A DNS-rebinding attacker could return a public
IP during validation and a private IP at fetch. Closing this would require
pinning the validated IP via a custom httpx Transport. Accepted residual.
"""

import hashlib
import ipaddress
import os
import socket
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

# Maximum download size: 100 MB
MAX_DOWNLOAD_SIZE = 100 * 1024 * 1024

# Maximum number of HTTP redirects to follow
MAX_REDIRECTS = 10

# ── SSRF floor ────────────────────────────────────────────────────────────
# Always enforced; not removable via user config. Any DNS-resolved IP that
# falls into one of these ranges is rejected at validation time. Keeping the
# list explicit (rather than relying on Python's `ipaddress.is_private` etc.)
# lets SECURITY-AUDIT.md name each range precisely.
SSRF_FLOOR_CIDRS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("127.0.0.0/8"),     # IPv4 loopback
    ipaddress.ip_network("10.0.0.0/8"),      # RFC 1918 class A
    ipaddress.ip_network("172.16.0.0/12"),   # RFC 1918 class B
    ipaddress.ip_network("192.168.0.0/16"),  # RFC 1918 class C
    ipaddress.ip_network("169.254.0.0/16"),  # IPv4 link-local (incl. 169.254.169.254 cloud metadata)
    ipaddress.ip_network("0.0.0.0/8"),       # "This network" — common misconfig
    ipaddress.ip_network("::1/128"),         # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),        # IPv6 unique local (ULA)
    ipaddress.ip_network("fe80::/10"),       # IPv6 link-local
)


class URLFetcher:
    """
    Fetches PDFs from URLs and caches them locally.
    """

    def __init__(self, cache_dir: Path | None = None, timeout: int = 60):
        """
        Initialize URL fetcher.

        Args:
            cache_dir: Directory to store downloaded PDFs. Defaults to temp dir.
            timeout: HTTP timeout in seconds
        """
        if cache_dir is None:
            cache_dir = Path(tempfile.gettempdir()) / "pdf-mcp" / "downloads"

        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # Restrict permissions on cache directory so other users can't read downloads
        os.chmod(self.cache_dir, 0o700)
        self.timeout = timeout
        self._url_to_path: dict[str, Path] = {}

    @staticmethod
    def _resolve_host_ips(hostname: str) -> list[str]:
        """Resolve hostname to all (IPv4+IPv6) IP strings.

        If the hostname is already an IP literal, returns [hostname] without
        a DNS lookup. Raises OSError if DNS resolution fails.
        """
        # net.isIP-equivalent: if it already parses as an IP, skip DNS.
        try:
            ipaddress.ip_address(hostname)
            return [hostname]
        except ValueError:
            pass
        addr_infos = socket.getaddrinfo(hostname, None)
        return list({info[4][0] for info in addr_infos})

    @staticmethod
    def _ip_in_floor(ip_str: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network | None:
        """Return the SSRF_FLOOR_CIDRS entry this IP falls into, or None."""
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return None
        for net in SSRF_FLOOR_CIDRS:
            if isinstance(ip, ipaddress.IPv4Address) and isinstance(
                net, ipaddress.IPv4Network
            ):
                if ip in net:
                    return net
            elif isinstance(ip, ipaddress.IPv6Address) and isinstance(
                net, ipaddress.IPv6Network
            ):
                if ip in net:
                    return net
        return None

    def _validate_url(self, url: str) -> None:
        """Validate URL against the hardened floor: https-only + SSRF deny.

        Floor is non-removable. User allow/deny rules (Phase 4) layer on top;
        they can *further restrict* but cannot loosen this.

        Raises:
            ValueError: If URL is malformed, non-https, or resolves to a
                blocked address.
        """
        try:
            parsed = urlparse(url)
        except ValueError as exc:
            raise ValueError(f"Invalid URL: {url}: {exc}") from exc

        if parsed.scheme != "https":
            raise ValueError(
                f"Only https:// URLs are allowed (got '{parsed.scheme}:'): {url}"
            )

        hostname = parsed.hostname
        if not hostname:
            raise ValueError(f"Could not extract hostname from URL: {url}")

        try:
            resolved_ips = self._resolve_host_ips(hostname)
        except OSError as exc:
            raise ValueError(
                f"DNS lookup for '{hostname}' failed: {exc}"
            ) from exc

        for ip in resolved_ips:
            blocked = self._ip_in_floor(ip)
            if blocked is not None:
                raise ValueError(
                    f"URL '{url}' host '{hostname}' resolves to {ip}, "
                    f"which is in a blocked range ({blocked}). SSRF floor."
                )

    def _get_cache_filename(self, url: str) -> str:
        """Generate cache filename from URL."""
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]

        # Try to extract original filename from URL
        parsed = urlparse(url)
        path = parsed.path

        if path.endswith(".pdf"):
            original_name = os.path.basename(path)
            # Sanitize filename
            safe_name = "".join(c for c in original_name if c.isalnum() or c in "._-")
            return f"{url_hash}_{safe_name}"

        return f"{url_hash}.pdf"

    def is_url(self, source: str) -> bool:
        """Check if source is a URL.

        Returns True for both http:// and https:// prefixes so that
        non-https URLs are still routed to the fetcher — which will then
        reject them via _validate_url with a clear error. This gives the
        user an informative failure ('https-only') instead of a confusing
        'file not found' that would happen if we routed http:// to the
        local-path branch.
        """
        return source.startswith(("http://", "https://"))

    def get_local_path(self, url: str) -> Path | None:
        """
        Get local path for a URL if already downloaded.

        Args:
            url: URL to check

        Returns:
            Local path if cached, None otherwise
        """
        if url in self._url_to_path:
            path = self._url_to_path[url]
            if path.exists():
                return path

        # Check disk cache
        filename = self._get_cache_filename(url)
        path = self.cache_dir / filename

        if path.exists():
            self._url_to_path[url] = path
            return path

        return None

    def fetch(self, url: str, force_refresh: bool = False) -> Path:
        """
        Fetch PDF from URL and return local path.

        Args:
            url: URL to fetch
            force_refresh: If True, re-download even if cached

        Returns:
            Path to local PDF file

        Raises:
            httpx.HTTPError: If download fails
            ValueError: If URL doesn't return a PDF or targets a blocked address
        """
        # Validate URL to prevent SSRF
        self._validate_url(url)

        # Check cache first
        if not force_refresh:
            cached_path = self.get_local_path(url)
            if cached_path:
                return cached_path

        # Download with manual redirect handling to validate each hop
        # before connecting (prevents TOCTOU SSRF via redirects)
        current_url = url
        with httpx.Client(timeout=self.timeout, follow_redirects=False) as client:
            for _ in range(MAX_REDIRECTS):
                with client.stream("GET", current_url) as response:
                    if response.is_redirect:
                        next_req = response.next_request
                        if next_req is None:
                            raise ValueError("Redirect with no target URL")
                        redirect_url = str(next_req.url)
                        self._validate_url(redirect_url)
                        current_url = redirect_url
                        continue

                    response.raise_for_status()

                    # Check Content-Length header if available
                    content_length = response.headers.get("content-length")
                    if content_length and int(content_length) > MAX_DOWNLOAD_SIZE:
                        raise ValueError(
                            f"PDF file too large: {int(content_length)} bytes "
                            f"(max {MAX_DOWNLOAD_SIZE} bytes)"
                        )

                    # Read response with size limit
                    chunks: list[bytes] = []
                    total_size = 0
                    for chunk in response.iter_bytes(chunk_size=8192):
                        total_size += len(chunk)
                        if total_size > MAX_DOWNLOAD_SIZE:
                            raise ValueError(
                                f"PDF download exceeded maximum size of "
                                f"{MAX_DOWNLOAD_SIZE} bytes"
                            )
                        chunks.append(chunk)

                    content = b"".join(chunks)

                    # Verify content type
                    content_type = response.headers.get("content-type", "")
                    if "pdf" not in content_type.lower():
                        # Check magic bytes when Content-Type is not PDF
                        if not content.startswith(b"%PDF"):
                            raise ValueError(f"URL does not appear to be a PDF: {url}")
                    break
            else:
                raise ValueError(f"Too many redirects (max {MAX_REDIRECTS})")

        # Save to cache with restricted permissions
        filename = self._get_cache_filename(url)
        local_path = self.cache_dir / filename

        fd = os.open(str(local_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, content)
        finally:
            os.close(fd)

        self._url_to_path[url] = local_path

        return local_path

    def clear_cache(self) -> int:
        """
        Clear all downloaded PDFs.

        Returns:
            Number of files deleted
        """
        count = 0
        for path in self.cache_dir.glob("*.pdf"):
            try:
                path.unlink()
                count += 1
            except OSError:
                pass

        self._url_to_path.clear()
        return count

    def get_cache_stats(self) -> dict[str, Any]:
        """Get statistics about URL cache."""
        files = list(self.cache_dir.glob("*.pdf"))
        total_size = sum(f.stat().st_size for f in files)

        return {
            "cached_files": len(files),
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "cache_dir": str(self.cache_dir),
        }
