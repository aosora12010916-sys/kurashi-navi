#!/usr/bin/env python3
"""毎ターン、事実に関する基本ルールを短く思い出させる（UserPromptSubmit）。

CLAUDE.md はセッション冒頭で読まれるが、長い作業では効きが薄れる。
数行だけ再注入して、金額・電話番号の推測書きを抑える。
"""
import json
import sys

REMINDER = (
    "【このリポジトリの前提】掲載内容は住民の生活に直結します。"
    "金額・電話番号・担当課・年齢や期間の要件・法令名は、"
    "一次情報（稲美町公式／兵庫県／所管省庁）で確認できたものだけを書くこと。"
    "確認できないものは推測で埋めず【要確認】を付けるか省く。"
    "既存の数字・固有名詞は出典なしに書き換えない。"
    "確認したら docs/fact-log.md に出典URLと確認日を追記する。"
)

sys.stdin.read()
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": REMINDER,
    },
    "suppressOutput": True,
}, ensure_ascii=False))
