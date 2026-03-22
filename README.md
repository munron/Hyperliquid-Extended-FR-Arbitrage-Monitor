# Hyperliquid × Extended — Commodity FR Arbitrage Monitor

Hyperliquid（Trade.xyz HIP-3 DEX）と Extended Exchange の間で、コモディティ先物のファンディングレート（FR）差を利用した**両建てアービトラージ機会**をモニターするスクリプトです。

---

## 概要

### アービトラージの仕組み

両取引所で同一コモディティの先物を**逆方向で両建て**することで、価格リスクをヘッジしながらFR差を収益として受け取ります。

```
FR高い方 → ショート（FRを受け取る）
FR低い方 → ロング（FRを支払う、または受け取る）
純収益   = |FR_高 − FR_低|
```

FRの符号と損益の関係：

| ポジション | FR > 0 | FR < 0 |
|-----------|--------|--------|
| ロング    | 支払い | 受取り |
| ショート  | 受取り | 支払い |

### 対象取引所

| 取引所 | 種別 | URL |
|--------|------|-----|
| Hyperliquid Trade.xyz DEX | HIP-3 拡張DEX | https://app.hyperliquid.xyz |
| Extended Exchange | StarkNet ベース Perp DEX | https://app.extended.exchange/perp |

### 対応コモディティペア

| 標準ティッカー | コモディティ | Hyperliquid (xyz DEX) | Extended Exchange |
|--------------|------------|----------------------|------------------|
| XAU | Gold（金） | `xyz:GOLD` | `XAU-USD` |
| XAG | Silver（銀） | `xyz:SILVER` | `XAG-USD` |
| XPT | Platinum（白金） | `xyz:PLATINUM` | `XPT-USD` |
| WTI | WTI Crude Oil（原油） | `xyz:CL` | `WTI-USD` |
| XBR | Brent Crude（ブレント原油） | `xyz:BRENTOIL` | `XBR-USD` |
| XNG | Natural Gas（天然ガス） | `xyz:NATGAS` | `XNG-USD` |
| XCU | Copper（銅） | `xyz:COPPER` | `XCU-USD` |

> **Note:** 両取引所でシンボル名が異なるため、スクリプト内で明示的にマッピングしています。

---

## セットアップ

### 必要環境

- Python 3.11 以上
- 外部ライブラリ不要（標準ライブラリのみ使用）

### インストール

```bash
git clone <repository>
cd fr-arb-hyperliquid-extended
```

### 設定

`.env` ファイルを編集して Discord Webhook URL を設定します：

```bash
# .env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_WEBHOOK_URL
```

Discord への通知が不要な場合は空欄のままで構いません。

---

## 使い方

```bash
python3 monitor_commodity_fr.py
```

---

## 出力の見方

### サマリーテーブル

```
  Ticker Commodity        現在APY     7h平均    24h平均     1W平均   basis
  ────── ──────────────  ─────────  ─────────  ─────────  ─────────  ──────
  XAG    Silver           +40.1%     +27.4%     +52.1%     +26.0%   0.086%
  XPT    Platinum         +37.2%     +36.2%     +59.1%      +1.3%   0.043%
```

| 列 | 説明 |
|----|------|
| 現在APY | 現時点のFR差を年利換算 |
| 7h平均 | 過去7時間のFR差の平均（年利換算） |
| 24h平均 | 過去24時間のFR差の平均（年利換算） |
| 1W平均 | 過去1週間のFR差の平均（年利換算） |
| basis | 両取引所の価格差（%）。スリッページ・リスクの目安 |

### 詳細セクション

```
  XAG  Silver
    スプレッド  現在: +40.1%  7h平均: +27.4%  24h平均: +52.1%  1W平均: +26.0%
    LONG   Hyperliquid xyz:SILVER     FR=+0.000625%  APY=+5.48%   ↓支払0.000625%/h
    SHORT    Extended XAG-USD        FR=+0.005200%  APY=+45.55%  ↑受取0.005200%/h
    価格  LONG側: $67.937  SHORT側: $67.879  basis 0.086%
```

| 表示 | 説明 |
|------|------|
| `↑受取` | そのポジションでFRを受け取る（プラス収益） |
| `↓支払` | そのポジションでFRを支払う（マイナス収益） |
| `⚠ basis X%` | 価格差が0.5%超で警告表示。ポジション解消時のスリッページに注意 |

### カラーコード（APY）

| 色 | 閾値 |
|----|------|
| 🟢 緑 | 20% 以上 |
| 🟡 黄 | 10% 以上 |
| 🟠 オレンジ | 10% 未満 |

### N/A について

過去データが不足している場合（銘柄の上場日が浅いなど）は `N/A` と表示されます。

---

## FR の計算式

```
年利 (APY) = FR(1h) × 24 × 365 × 100
日次       = FR(1h) × 24 × 100
```

Extended Exchange の FR は1時間ごとに課金されます。UIに「8 hours」と表示されるのは8時間換算の参考値であり、APIが返す `fundingRate` は1時間単位です。

---

## Discord 通知

`DISCORD_WEBHOOK_URL` が設定されている場合、スクリプト実行のたびにDiscordへ結果を送信します。

- プラスのスプレッドがある銘柄のみ通知
- 銘柄ごとに個別の埋め込みメッセージ（Embed）
- 現在値・7h/24h/1W平均・ベーシスリスクを含む

定期実行する場合は cron などを利用してください：

```bash
# 毎時5分に実行する例
5 * * * * cd /path/to/fr-arb-hyperliquid-extended && python3 monitor_commodity_fr.py
```

---

## API リファレンス

### Hyperliquid

| エンドポイント | 用途 |
|--------------|------|
| `POST https://api.hyperliquid.xyz/info` `{"type":"metaAndAssetCtxs","dex":"xyz"}` | 現在のFR・価格取得 |
| `POST https://api.hyperliquid.xyz/info` `{"type":"fundingHistory","coin":"...","startTime":...,"endTime":...}` | FR履歴取得 |

### Extended Exchange

| エンドポイント | 用途 |
|--------------|------|
| `GET https://api.starknet.extended.exchange/api/v1/info/markets` | 全市場の現在FR・価格取得 |
| `GET https://api.starknet.extended.exchange/api/v1/info/{market}/funding?startTime={ms}&endTime={ms}` | FR履歴取得（`startTime`・`endTime` 両方必須、エポックミリ秒） |

---

## リスクの注意事項

- **ベーシスリスク**: 両取引所の価格差（basis）が大きいほど、ポジション解消時のスリッページが大きくなります。目安として0.5%超は注意。
- **FR変動リスク**: FRは毎時間変動します。現在値が高くても継続するとは限りません。1W平均と比較して判断してください。
- **流動性リスク**: Open Interest が小さい銘柄はスリッページが大きくなる可能性があります。
- **スマートコントラクトリスク**: Extended Exchange は StarkNet 上で動作しており、チェーン固有のリスクがあります。
- **清算リスク**: 両建てでもどちらかの取引所で強制清算が発生するとヘッジが崩れます。証拠金管理に注意してください。
