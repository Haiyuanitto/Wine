#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import dataclasses
import datetime as dt
import json
import pathlib
import re
import statistics
import time
import urllib.parse
from collections import defaultdict
from typing import Iterable

import requests
import yaml
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

PRICE_PATTERN = re.compile(r"(?P<cur>HK\$|US\$|€|£|¥|JPY|CNY|RMB|AUD|CAD|CHF|SGD|HKD|USD)?\s*(?P<value>\d[\d,]*\.?\d*)")
VINTAGE_PATTERN = re.compile(r"\b(19[5-9]\d|20[0-2]\d)\b")

CURRENCY_MAP = {
    "HK$": "HKD",
    "US$": "USD",
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "RMB": "CNY",
    "CNY": "CNY",
    "HKD": "HKD",
    "USD": "USD",
    "AUD": "AUD",
    "CAD": "CAD",
    "CHF": "CHF",
    "SGD": "SGD",
    "JPY": "JPY",
}

DEFAULT_FX_TO_HKD = {
    "HKD": 1.0,
    "USD": 7.8,
    "EUR": 8.4,
    "GBP": 9.9,
    "JPY": 0.052,
    "CNY": 1.08,
    "AUD": 5.1,
    "CAD": 5.7,
    "CHF": 8.8,
    "SGD": 5.8,
}


@dataclasses.dataclass
class Offer:
    wine_name: str
    vintage: int | None
    price: float
    currency: str
    merchant: str
    location: str
    source_url: str
    query: str

    @property
    def is_hk(self) -> bool:
        text = f"{self.location} {self.merchant}".lower()
        return "hong kong" in text or re.search(r"\bhk\b", text) is not None


@dataclasses.dataclass
class Deal:
    wine_name: str
    vintage: int
    baseline_hkd: float
    offer_hkd: float
    saving_hkd: float
    saving_pct: float
    offer: Offer


def load_config(path: pathlib.Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError("Config 文件格式无效")
    return cfg


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def build_search_url(query: str) -> str:
    q = urllib.parse.quote_plus(query)
    return f"https://www.wine-searcher.com/find/{q}"


def extract_vintage(*texts: str) -> int | None:
    for text in texts:
        if not text:
            continue
        m = VINTAGE_PATTERN.search(text)
        if m:
            return int(m.group(1))
    return None


def canonical_wine_name(text: str) -> str:
    cleaned = VINTAGE_PATTERN.sub(" ", text)
    return normalize_space(cleaned).lower()


def parse_price(text: str) -> tuple[float, str] | None:
    m = PRICE_PATTERN.search(text)
    if not m:
        return None

    raw_val = m.group("value").replace(",", "")
    try:
        value = float(raw_val)
    except ValueError:
        return None

    cur = (m.group("cur") or "USD").upper()
    cur = CURRENCY_MAP.get(cur, cur)
    return value, cur


def fetch_html(session: requests.Session, url: str, timeout_sec: int) -> str:
    resp = session.get(url, timeout=timeout_sec)
    resp.raise_for_status()
    return resp.text


def extract_offers_from_jsonld(soup: BeautifulSoup, url: str, query: str) -> list[Offer]:
    offers: list[Offer] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.get_text(strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            wine_name = normalize_space(str(item.get("name", "")))
            if not wine_name:
                continue
            vintage = extract_vintage(wine_name, query)

            item_offers = item.get("offers", [])
            if isinstance(item_offers, dict):
                item_offers = [item_offers]

            for oi in item_offers:
                if not isinstance(oi, dict):
                    continue
                price = oi.get("price")
                cur = oi.get("priceCurrency")
                if price is None:
                    continue
                try:
                    price_f = float(str(price).replace(",", ""))
                except ValueError:
                    continue
                currency = CURRENCY_MAP.get(str(cur).upper(), str(cur).upper() if cur else "USD")
                merchant = ""
                location = ""
                seller = oi.get("seller")
                if isinstance(seller, dict):
                    merchant = normalize_space(str(seller.get("name", "")))
                    addr = seller.get("address")
                    if isinstance(addr, dict):
                        location = normalize_space(str(addr.get("addressCountry", "")))
                offers.append(
                    Offer(
                        wine_name=wine_name,
                        vintage=vintage,
                        price=price_f,
                        currency=currency,
                        merchant=merchant,
                        location=location,
                        source_url=url,
                        query=query,
                    )
                )
    return offers


def extract_offers_from_dom(soup: BeautifulSoup, url: str, query: str) -> list[Offer]:
    offers: list[Offer] = []
    seen: set[tuple[str, float, str, str]] = set()

    selectors = [
        "tr[class*='offer']",
        "tr[class*='record']",
        "div[class*='offer']",
        "div[class*='merchant']",
        "li[class*='offer']",
    ]

    nodes = []
    for sel in selectors:
        nodes.extend(soup.select(sel))

    for node in nodes:
        text = normalize_space(node.get_text(" ", strip=True))
        if not text:
            continue

        parsed = parse_price(text)
        if not parsed:
            continue
        price, currency = parsed

        wine_name = ""
        merchant = ""
        location = ""

        for wsel in ["a[href*='/find/']", "[class*='wine']", "[class*='name']"]:
            tag = node.select_one(wsel)
            if tag:
                wine_name = normalize_space(tag.get_text(" ", strip=True))
                if wine_name:
                    break

        if not wine_name:
            wine_name = normalize_space(query)
        vintage = extract_vintage(wine_name, text, query)

        for msel in ["[class*='merchant']", "[class*='retailer']", "[class*='seller']"]:
            tag = node.select_one(msel)
            if tag:
                merchant = normalize_space(tag.get_text(" ", strip=True))
                if merchant:
                    break

        for lsel in ["[class*='location']", "[class*='country']", "[class*='region']"]:
            tag = node.select_one(lsel)
            if tag:
                location = normalize_space(tag.get_text(" ", strip=True))
                if location:
                    break

        if not location:
            mloc = re.search(r"(?:from|in)\s+([A-Za-z][A-Za-z\s-]+)$", text, re.IGNORECASE)
            if mloc:
                location = normalize_space(mloc.group(1))

        key = (wine_name, price, currency, merchant)
        if key in seen:
            continue
        seen.add(key)

        offers.append(
            Offer(
                wine_name=wine_name,
                vintage=vintage,
                price=price,
                currency=currency,
                merchant=merchant,
                location=location,
                source_url=url,
                query=query,
            )
        )

    return offers


def extract_offers(html: str, url: str, query: str) -> list[Offer]:
    soup = BeautifulSoup(html, "html.parser")
    offers = extract_offers_from_jsonld(soup, url, query)
    offers.extend(extract_offers_from_dom(soup, url, query))
    return offers


def is_target_burgundy(wine_name: str, producers: Iterable[str], keywords: Iterable[str]) -> bool:
    lower = wine_name.lower()
    producer_hit = any(p.lower() in lower for p in producers)
    kw_hit = any(k.lower() in lower for k in keywords)
    return producer_hit or kw_hit


def is_excluded_wine(wine_name: str, exclude_name_patterns: Iterable[str]) -> bool:
    lower = wine_name.lower()
    return any(p.lower() in lower for p in exclude_name_patterns)


def to_hkd(amount: float, currency: str, fx_to_hkd: dict[str, float]) -> float | None:
    rate = fx_to_hkd.get(currency.upper())
    if rate is None:
        return None
    return amount * rate


def find_deals(
    offers: list[Offer],
    producers: list[str],
    keywords: list[str],
    exclude_name_patterns: list[str],
    fx_to_hkd: dict[str, float],
    min_saving_pct: float,
) -> list[Deal]:
    grouped: dict[tuple[str, int], list[Offer]] = defaultdict(list)
    for o in offers:
        if is_excluded_wine(o.wine_name, exclude_name_patterns):
            continue
        if not is_target_burgundy(o.wine_name, producers, keywords):
            continue
        if o.vintage is None:
            continue
        grouped[(canonical_wine_name(o.wine_name), o.vintage)].append(o)

    deals: list[Deal] = []
    for (_, vintage), bucket in grouped.items():
        hk_prices = []
        for o in bucket:
            if not o.is_hk:
                continue
            val_hkd = to_hkd(o.price, o.currency, fx_to_hkd)
            if val_hkd is not None:
                hk_prices.append(val_hkd)

        if not hk_prices:
            continue

        baseline = statistics.median(hk_prices)
        display_name = max(
            (o.wine_name for o in bucket if o.wine_name),
            key=len,
            default="Unknown Wine",
        )

        for o in bucket:
            offer_hkd = to_hkd(o.price, o.currency, fx_to_hkd)
            if offer_hkd is None:
                continue
            saving = baseline - offer_hkd
            saving_pct = saving / baseline if baseline > 0 else 0.0
            if saving_pct >= min_saving_pct and saving > 0:
                deals.append(
                    Deal(
                        wine_name=display_name,
                        vintage=vintage,
                        baseline_hkd=baseline,
                        offer_hkd=offer_hkd,
                        saving_hkd=saving,
                        saving_pct=saving_pct,
                        offer=o,
                    )
                )

    deals.sort(key=lambda d: d.saving_hkd, reverse=True)
    return deals


def write_outputs(deals: list[Deal], output_dir: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"deals_{ts}.csv"
    md_path = output_dir / f"deals_{ts}.md"

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "wine_name",
            "vintage",
            "baseline_hkd",
            "offer_hkd",
            "saving_hkd",
            "saving_pct",
            "merchant",
            "location",
            "currency",
            "raw_price",
            "source_url",
            "query",
        ])
        for d in deals:
            writer.writerow([
                d.wine_name,
                d.vintage,
                f"{d.baseline_hkd:.2f}",
                f"{d.offer_hkd:.2f}",
                f"{d.saving_hkd:.2f}",
                f"{d.saving_pct:.2%}",
                d.offer.merchant,
                d.offer.location,
                d.offer.currency,
                f"{d.offer.price:.2f}",
                d.offer.source_url,
                d.offer.query,
            ])

    lines = [
        f"# Wine-Searcher 自动比价报告 ({dt.datetime.now().isoformat(timespec='seconds')})",
        "",
        f"共发现 **{len(deals)}** 条低于香港市场基准价的报价（仅同年份比较）。",
        "",
        "| 酒款 | 年份 | 香港基准价(HKD) | 报价(HKD) | 节省(HKD) | 节省比例 | 商家 | 地区 |",
        "|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for d in deals[:100]:
        lines.append(
            "| {wine} | {vintage} | {base:.2f} | {offer:.2f} | {save:.2f} | {pct:.2%} | {mer} | {loc} |".format(
                wine=d.wine_name,
                vintage=d.vintage,
                base=d.baseline_hkd,
                offer=d.offer_hkd,
                save=d.saving_hkd,
                pct=d.saving_pct,
                mer=d.offer.merchant or "-",
                loc=d.offer.location or "-",
            )
        )

    with md_path.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return csv_path, md_path


def run_once(config: dict) -> tuple[int, pathlib.Path, pathlib.Path]:
    producers = config.get("famous_producers", [])
    keywords = config.get("keywords", [])
    exclude_name_patterns = config.get("exclude_name_patterns", [])
    timeout_sec = int(config.get("timeout_sec", 25))
    min_saving_pct = float(config.get("min_saving_pct", 0.0))
    queries = config.get("queries")

    if not queries:
        queries = [f"{p} burgundy" for p in producers]

    fx_to_hkd = dict(DEFAULT_FX_TO_HKD)
    fx_to_hkd.update(config.get("fx_to_hkd", {}))

    output_dir = pathlib.Path(config.get("output_dir", "output"))

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    all_offers: list[Offer] = []

    for q in queries:
        url = build_search_url(q)
        try:
            html = fetch_html(session, url, timeout_sec=timeout_sec)
            offers = extract_offers(html, url, q)
            all_offers.extend(offers)
            print(f"[OK] query={q} offers={len(offers)}")
        except Exception as e:  # noqa: BLE001
            print(f"[ERR] query={q} url={url} error={e}")

    deals = find_deals(
        all_offers,
        producers=producers,
        keywords=keywords,
        exclude_name_patterns=exclude_name_patterns,
        fx_to_hkd=fx_to_hkd,
        min_saving_pct=min_saving_pct,
    )

    csv_path, md_path = write_outputs(deals, output_dir)
    print(f"[DONE] deals={len(deals)} csv={csv_path} md={md_path}")
    return len(deals), csv_path, md_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Wine-Searcher 勃艮第名庄自动比价")
    parser.add_argument("--config", default="config/config.yaml", help="配置文件路径")
    parser.add_argument(
        "--watch-hours",
        type=float,
        default=0,
        help="轮询间隔（小时）。0 表示只运行一次。",
    )
    args = parser.parse_args()

    cfg_path = pathlib.Path(args.config)
    if not cfg_path.exists():
        raise FileNotFoundError(f"找不到配置文件: {cfg_path}")

    while True:
        cfg = load_config(cfg_path)
        run_once(cfg)

        if args.watch_hours <= 0:
            break

        sleep_sec = int(args.watch_hours * 3600)
        print(f"[SLEEP] {sleep_sec} seconds")
        time.sleep(sleep_sec)


if __name__ == "__main__":
    main()
