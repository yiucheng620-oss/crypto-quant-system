#!/usr/bin/env python3
"""On-Chain Data Collector — BTC & ETH metrics
Uses free public APIs: CoinGecko, Blockchain.info, Mempool.space, Etherscan (free tier).
No API keys required.
Saves to ~/workspace/onchain_data/onchain_latest.json
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error

OUTPUT_DIR = os.path.expanduser("~/workspace/onchain_data")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "onchain_latest.json")


def fetch_json(url: str, timeout: int = 15) -> dict | list | None:
    """Fetch JSON from a URL with error handling."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; HermesAgent/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError) as e:
        print(f"  [WARN] {url[:60]}... : {e}", file=sys.stderr)
        return None


def collect_blockchain_info_stats() -> dict:
    """BTC stats from blockchain.info (free, no key)."""
    print("[onchain] Fetching blockchain.info stats...")
    data = fetch_json("https://api.blockchain.info/stats")
    if data is None:
        return {"error": "No data"}

    return {
        "hash_rate": data.get("hash_rate"),           # GH/s
        "hash_rate_petahash_per_sec": round(data.get("hash_rate", 0) / 1_000_000, 2) if data.get("hash_rate") else None,
        "difficulty": data.get("difficulty"),
        "estimated_transaction_volume_btc": data.get("estimated_transaction_volume_usd"),
        "total_btc_mined": data.get("totalbc"),
        "market_price_usd": data.get("market_price_usd"),
        "trade_volume_btc": data.get("trade_volume_btc"),
        "n_transactions": data.get("n_tx"),            # Total transactions
        "n_transactions_per_day": data.get("n_tx") // (int(time.time()) // 86400) if data.get("n_tx") else None,  # rough daily avg
        "mempool_size": data.get("mempool_size"),      # bytes
        "mempool_size_mb": round(data.get("mempool_size", 0) / 1_000_000, 2) if data.get("mempool_size") else None,
        "next_retarget_estimate": data.get("nextretargettime_estimate"),
        "timestamp_unix": data.get("timestamp"),
        "source": "blockchain.info/stats",
    }


def collect_mempool_space() -> dict:
    """BTC mempool data from mempool.space (free, no key)."""
    print("[onchain] Fetching mempool.space data...")
    mempool_data = fetch_json("https://mempool.space/api/v1/fees/recommended")
    blocks_data = fetch_json("https://mempool.space/api/v1/blocks")
    
    result = {}
    if mempool_data:
        result["fee_estimates"] = {
            "fastest_fee_sat_vbyte": mempool_data.get("fastestFee"),
            "half_hour_fee_sat_vbyte": mempool_data.get("halfHourFee"),
            "hour_fee_sat_vbyte": mempool_data.get("hourFee"),
            "economy_fee_sat_vbyte": mempool_data.get("economyFee"),
            "minimum_fee_sat_vbyte": mempool_data.get("minimumFee"),
        }

    if blocks_data and isinstance(blocks_data, list) and len(blocks_data) > 0:
        latest = blocks_data[0]
        result["latest_block"] = {
            "height": latest.get("height"),
            "timestamp": latest.get("timestamp"),
            "size": latest.get("size"),
            "tx_count": latest.get("tx_count"),
            "extras": latest.get("extras", {}),
        }

    if not mempool_data and not blocks_data:
        result["error"] = "No data"
    
    result["source"] = "mempool.space"
    return result


def collect_coingecko_btc() -> dict:
    """BTC market & on-chain data from CoinGecko free API."""
    print("[onchain] Fetching CoinGecko BTC data...")
    data = fetch_json("https://api.coingecko.com/api/v3/coins/bitcoin?localization=false&tickers=false&community_data=true&developer_data=true&sparkline=false")
    if data is None:
        return {"error": "No data"}

    md = data.get("market_data", {})
    cd = data.get("community_data", {})
    dd = data.get("developer_data", {})

    return {
        "market_cap_rank": data.get("market_cap_rank"),
        "market_data": {
            "price_usd": md.get("current_price", {}).get("usd"),
            "market_cap_usd": md.get("market_cap", {}).get("usd"),
            "total_volume_usd": md.get("total_volume", {}).get("usd"),
            "circulating_supply": md.get("circulating_supply"),
            "total_supply": md.get("total_supply"),
            "max_supply": md.get("max_supply"),
            "price_change_24h_pct": md.get("price_change_percentage_24h"),
            "price_change_7d_pct": md.get("price_change_percentage_7d"),
            "ath_usd": md.get("ath", {}).get("usd"),
            "ath_date": md.get("ath_date", {}).get("usd"),
        },
        "community_data": {
            "twitter_followers": cd.get("twitter_followers"),
            "reddit_subscribers": cd.get("reddit_subscribers"),
        },
        "developer_data": {
            "forks": dd.get("forks"),
            "stars": dd.get("stars"),
            "subscribers": dd.get("subscribers"),
            "total_issues": dd.get("total_issues"),
            "closed_issues": dd.get("closed_issues"),
            "pull_requests_merged": dd.get("pull_requests_merged"),
            "commit_count_4_weeks": dd.get("commit_count_4_weeks"),
        },
        "source": "coingecko",
    }


def collect_coingecko_eth() -> dict:
    """ETH market data from CoinGecko free API."""
    print("[onchain] Fetching CoinGecko ETH data...")
    data = fetch_json("https://api.coingecko.com/api/v3/coins/ethereum?localization=false&tickers=false&community_data=true&developer_data=true&sparkline=false")
    if data is None:
        return {"error": "No data"}

    md = data.get("market_data", {})

    return {
        "market_cap_rank": data.get("market_cap_rank"),
        "market_data": {
            "price_usd": md.get("current_price", {}).get("usd"),
            "market_cap_usd": md.get("market_cap", {}).get("usd"),
            "total_volume_usd": md.get("total_volume", {}).get("usd"),
            "circulating_supply": md.get("circulating_supply"),
            "price_change_24h_pct": md.get("price_change_percentage_24h"),
            "price_change_7d_pct": md.get("price_change_percentage_7d"),
        },
        "source": "coingecko",
    }


def collect_etherscan_eth() -> dict:
    """ETH chain stats from Etherscan free API (no key needed for basic requests)."""
    print("[onchain] Fetching Etherscan ETH stats...")
    # Etherscan free API — some endpoints need a key, but the basic stats endpoint
    # actually works without one for some queries
    # Alternative: use https://eth.blockscout.com/api/v1/ for open-source data
    data = fetch_json("https://api.blockchair.com/ethereum/stats")
    if data is None:
        return {"error": "No data from blockchair"}

    result = data.get("data", {})
    if not result:
        return {"error": "Unexpected blockchair format"}

    return {
        "difficulty": result.get("difficulty"),
        "hashrate_ghs": result.get("hashrate_24h"),
        "transaction_count_24h": result.get("transaction_count_24h"),
        "active_addresses_24h": result.get("address_count_24h"),
        "mempool_tx_count": result.get("mempool_transaction_count"),
        "block_time_seconds": result.get("block_time"),
        "total_transactions": result.get("transaction_count"),
        "source": "blockchair.com/ethereum",
    }


def collect() -> dict:
    """Collect all on-chain data."""
    btc_blockchain = collect_blockchain_info_stats()
    btc_mempool = collect_mempool_space()
    btc_coingecko = collect_coingecko_btc()
    eth_coingecko = collect_coingecko_eth()
    eth_onchain = collect_etherscan_eth()

    # Print summary
    print("\n[onchain] Summary:")
    if "hash_rate" in btc_blockchain and btc_blockchain["hash_rate"]:
        print(f"  BTC Hashrate: {btc_blockchain.get('hash_rate_petahash_per_sec', '?')} PH/s")
    if "mempool_size_mb" in btc_blockchain and btc_blockchain["mempool_size_mb"]:
        print(f"  BTC Mempool: {btc_blockchain['mempool_size_mb']} MB")
    if "fee_estimates" in btc_mempool:
        fe = btc_mempool["fee_estimates"]
        print(f"  BTC Fees (fast/med/slow): {fe.get('fastest_fee_sat_vbyte', '?')}/{fe.get('half_hour_fee_sat_vbyte', '?')}/{fe.get('hour_fee_sat_vbyte', '?')} sat/vB")
    if "market_data" in btc_coingecko:
        bmd = btc_coingecko["market_data"]
        print(f"  BTC Price: ${bmd.get('price_usd', '?'):,.2f}" if bmd.get('price_usd') else "")
        print(f"  BTC 24h Change: {bmd.get('price_change_24h_pct', '?'):.2f}%" if bmd.get('price_change_24h_pct') else "")
    if "market_data" in eth_coingecko:
        emd = eth_coingecko["market_data"]
        print(f"  ETH Price: ${emd.get('price_usd', '?'):,.2f}" if emd.get('price_usd') else "")
    if "hashrate_ghs" in eth_onchain and eth_onchain.get("hashrate_ghs"):
        hr = eth_onchain["hashrate_ghs"]
        try:
            print(f"  ETH Hashrate: {float(hr):,.0f} GH/s")
        except (ValueError, TypeError):
            print(f"  ETH Hashrate: {hr} GH/s")

    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "unix_seconds": int(time.time()),
        "source": "blockchain.info + mempool.space + coingecko + blockchair",
        "btc": {
            "blockchain_info": btc_blockchain,
            "mempool_space": btc_mempool,
            "coingecko": btc_coingecko,
        },
        "eth": {
            "coingecko": eth_coingecko,
            "onchain": eth_onchain,
        },
    }


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    data = collect()
    with open(OUTPUT_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"\n[onchain] Saved to {OUTPUT_FILE}")
    print("[onchain] Done.")


if __name__ == "__main__":
    main()
