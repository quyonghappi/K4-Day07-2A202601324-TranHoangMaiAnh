#!/usr/bin/env python3
"""Crawl permitted public policy pages into the K4 corpus format.

The input is a CSV containing at least ``url``.  Optional columns are:
``doc_id``, ``title``, ``customer_role``, ``category``, ``language``,
``document_version`` and ``license_or_permission``.

This is intentionally a small, polite crawler, not a site-wide spider.  It
only fetches the URLs explicitly listed in the input CSV and writes cleaned
Markdown plus ``sources.csv`` compatible with DATA_COLLECTION.md.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser


USER_AGENT = "K4-Ecommerce-Corpus-Crawler/1.0 (+educational-lab)"
MANIFEST_FIELDS = [
    "doc_id", "file_path", "title", "source_url", "retrieved_at",
    "document_version", "license_or_permission",
]
ALLOWED_ROLES = {"buyer", "seller", "both"}
SKIP_TAGS = {"script", "style", "nav", "footer", "header", "noscript", "svg", "iframe"}
BLOCK_TAGS = {"p", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "div", "section", "article", "blockquote"}


class PolicyExtractor(HTMLParser):
    """Extract readable Markdown-like text while retaining headings and lists."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.buffer: list[str] = []
        self.skip_depth = 0
        self.title_parts: list[str] = []
        self.in_title = False
        self.list_depth = 0
        self.in_list_item = False

    def _flush(self) -> None:
        value = re.sub(r"\s+", " ", "".join(self.buffer)).strip()
        self.buffer.clear()
        if value:
            prefix = "- " if self.in_list_item else ""
            self.lines.append(prefix + value)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag == "title":
            self.in_title = True
        if tag in {"ul", "ol"}:
            self._flush()
            self.list_depth += 1
        elif tag == "li":
            self._flush()
            self.in_list_item = True
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._flush()
            self.buffer.append("#" * int(tag[1]) + " ")
        elif tag in BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag == "title":
            self.in_title = False
        if tag == "li":
            self._flush()
            self.in_list_item = False
        elif tag in {"ul", "ol"}:
            self._flush()
            self.list_depth = max(0, self.list_depth - 1)
        elif tag in BLOCK_TAGS or tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self.in_title:
            self.title_parts.append(data)
        self.buffer.append(data)

    def result(self) -> tuple[str, str]:
        self._flush()
        content: list[str] = []
        for line in self.lines:
            if line.startswith("#") and not line.startswith("# "):
                content.append(line)
            else:
                content.append(line)
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(content)).strip()
        title = " ".join("".join(self.title_parts).split())
        return title, text


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE)
    value = re.sub(r"[\s_]+", "-", value)
    return value.strip("-") or "document"


def yaml_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def load_input(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "url" not in reader.fieldnames:
            raise ValueError("Input CSV must contain a url column")
        rows = []
        for row in reader:
            cleaned = {key.strip(): (value or "").strip() for key, value in row.items() if key}
            if cleaned.get("url"):
                rows.append(cleaned)
        return rows


def allowed_by_robots(url: str, user_agent: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"unsupported URL: {url}")
    robots = RobotFileParser(f"{parsed.scheme}://{parsed.netloc}/robots.txt")
    try:
        robots.read()
    except (HTTPError, URLError, OSError) as exc:
        raise RuntimeError(f"cannot verify robots.txt: {exc}") from exc
    return robots.can_fetch(user_agent, url)


def fetch(url: str, user_agent: str, timeout: float) -> tuple[str, str]:
    request = Request(url, headers={"User-Agent": user_agent, "Accept": "text/html,text/plain;q=0.9"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - URLs are explicitly supplied by the user.
        content_type = response.headers.get_content_type().lower()
        if content_type not in {"text/html", "text/plain"}:
            raise ValueError(f"unsupported content type: {content_type}")
        encoding = response.headers.get_content_charset() or "utf-8"
        return response.geturl(), response.read().decode(encoding, errors="replace")


def existing_manifest(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {row["doc_id"]: row for row in csv.DictReader(handle) if row.get("doc_id")}


def write_manifest(path: Path, records: dict[str, dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        for doc_id in sorted(records):
            writer.writerow({field: records[doc_id].get(field, "") for field in MANIFEST_FIELDS})


def make_document(metadata: dict[str, str], content: str) -> str:
    front_matter = "\n".join(f"{key}: {yaml_quote(value)}" for key, value in metadata.items())
    return f"---\n{front_matter}\n---\n\n# {metadata['title']}\n\n{content}\n"


def process_row(row: dict[str, str], output_dir: Path, user_agent: str, timeout: float, overwrite: bool) -> dict[str, str]:
    role = row.get("customer_role", "").lower()
    if role not in ALLOWED_ROLES:
        raise ValueError("customer_role must be buyer, seller or both")
    if not allowed_by_robots(row["url"], user_agent):
        raise PermissionError("disallowed by robots.txt")
    final_url, body = fetch(row["url"], user_agent, timeout)
    extracted_title, content = PolicyExtractorFrom(body)
    if len(content) < 80:
        raise ValueError("cleaned content is too short")
    title = row.get("title") or extracted_title or row.get("doc_id", "policy").replace("-", " ").title()
    doc_id = slugify(row.get("doc_id") or title)
    metadata = {
        "doc_id": doc_id,
        "title": title,
        "customer_role": role,
        "category": row.get("category", "ecommerce-policy"),
        "language": row.get("language", "vi"),
        "source_url": final_url,
        "retrieved_at": row.get("retrieved_at") or date.today().isoformat(),
        "document_version": row.get("document_version") or "not-stated",
    }
    output_path = output_dir / f"{doc_id}.md"
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"{output_path} exists; use --overwrite to replace it")
    output_path.write_text(make_document(metadata, content), encoding="utf-8")
    return {
        "doc_id": doc_id, "file_path": output_path.as_posix(), "title": title,
        "source_url": final_url, "retrieved_at": metadata["retrieved_at"],
        "document_version": metadata["document_version"],
        "license_or_permission": row.get("license_or_permission", "public-page"),
    }


def PolicyExtractorFrom(body: str) -> tuple[str, str]:
    parser = PolicyExtractor()
    parser.feed(body)
    parser.close()
    return parser.result()


def main() -> int:
    parser = argparse.ArgumentParser(description="Crawl explicitly listed public K4 e-commerce pages")
    parser.add_argument("input_csv", type=Path, help="CSV with url and K4 metadata columns")
    parser.add_argument("--output-dir", type=Path, default=Path("data/k4_ecommerce"))
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--user-agent", default=USER_AGENT)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.delay < 1:
        parser.error("--delay must be at least 1 second")
    if not args.input_csv.is_file():
        parser.error(f"input file not found: {args.input_csv}")
    try:
        rows = load_input(args.input_csv)
    except ValueError as exc:
        parser.error(str(exc))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "sources.csv"
    manifest = existing_manifest(manifest_path)
    failures = 0
    for index, row in enumerate(rows):
        if index:
            time.sleep(args.delay)
        try:
            record = process_row(row, args.output_dir, args.user_agent, args.timeout, args.overwrite)
            manifest[record["doc_id"]] = record
            print(f"Saved {record['file_path']}")
        except (HTTPError, URLError, OSError, RuntimeError, ValueError, PermissionError) as exc:
            failures += 1
            print(f"Skipped {row.get('url', '<missing url>')}: {exc}", file=sys.stderr)
    write_manifest(manifest_path, manifest)
    print(f"Manifest updated: {manifest_path}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
