#!/usr/bin/env python3
"""くらしの相談ナビ 事実チェッカー

追加された行の中から「裏取りが必要な事実」（金額・電話番号・要件・法令）を
拾い出し、出典台帳 docs/fact-log.md に記録がなく、かつ【要確認】も付いて
いないものを未確認として報告する。

  postedit … Edit/Write 直後。警告をモデルの文脈へ返す（ブロックはしない）
  stopgate … 応答終了時。未確認が残っていれば作業を止める
  report   … 人間／スラッシュコマンド向けにテキストで一覧表示

判定はすべて「HEAD（または origin/main）からの追加行」に対して行うので、
既存の記述が蒸し返されることはない。
"""
import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FACT_LOG = os.path.join(REPO, "docs", "fact-log.md")
TARGET_SUFFIX = ".html"

# 裏取りが要る事実の型。ラベルは警告文にそのまま出る。
PATTERNS = [
    ("電話番号", re.compile(r"0\d{1,4}-\d{2,4}-\d{4}")),
    ("金額", re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:万円|円)")),
    ("割合", re.compile(r"\d+(?:\.\d+)?\s*(?:％|%|割)")),
    ("年齢・期間の要件", re.compile(r"\d+\s*(?:歳|か月|ヶ月|年度|級)")),
    ("法令・改定時期", re.compile(r"(?:令和|平成)\s*\d+\s*年(?:\s*\d+\s*月)?|第\s*\d+\s*条")),
]

# 日本語を含まない行（CSS/JS）は対象外。加えて見た目の指定は明示的に除外する。
JAPANESE = re.compile(r"[ぁ-んァ-ヶ一-龥]")
STYLE_NOISE = re.compile(
    r"(?:px|rem|em|vh|vw)\b|#[0-9a-fA-F]{3,8}\b|z-index|font-size|line-height"
    r"|margin|padding|border-radius|rgba?\(|translate|@media"
)
# 制度データの項目名。日本語を含まない tel: なども取りこぼさないため
DATA_FIELD = re.compile(
    r"\b(?:tel|address|contact|name|hours|note|summary|target|support|flow|cost"
    r"|meyasu|basis|staffNote|detail|docs|tips)\s*:"
)
# 出典が示されていれば確認済みとみなす手がかり
SOURCE_HINT = re.compile(r"【要確認】|出典|根拠：|https?://")


def run(args):
    try:
        out = subprocess.run(
            args, cwd=REPO, capture_output=True, text=True, timeout=20
        )
        return out.stdout if out.returncode == 0 else ""
    except Exception:
        return ""


def diff_base(wide):
    """比較元を決める。wide=True ならブランチ全体（コミット済みも含む）を見る。"""
    if wide:
        for ref in ("origin/main", "main"):
            base = run(["git", "merge-base", "HEAD", ref]).strip()
            if base:
                return base
    return "HEAD"


def diff_lines(wide):
    """追加行と削除行を (ファイル名, 行番号, 本文) の一覧で返す。"""
    base = diff_base(wide)
    diff = run(["git", "diff", "-U0", base, "--"])
    if wide:
        # ブランチ全体を見るときは未コミットの変更も足す
        diff += run(["git", "diff", "-U0", "HEAD", "--"])
    added, removed, path, lineno = [], [], None, 0
    for raw in diff.splitlines():
        if raw.startswith("+++ b/"):
            path = raw[6:]
        elif raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            lineno = int(m.group(1)) if m else 0
        elif raw.startswith("+") and not raw.startswith("+++"):
            if path and path.endswith(TARGET_SUFFIX):
                added.append((path, lineno, raw[1:]))
            lineno += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            if path and path.endswith(TARGET_SUFFIX):
                removed.append((path, lineno, raw[1:]))
    return base, added, removed


def file_text(path):
    try:
        with open(os.path.join(REPO, path), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def base_text(base, path):
    """比較元コミット時点のファイル内容。もともと載っていた値の判定に使う。"""
    return run(["git", "show", f"{base}:{path}"])


def fact_log_text():
    try:
        with open(FACT_LOG, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


SEGMENT_SPLIT = re.compile(r"。|\\\\n|\n")


def segments(text):
    """行を文に割る。【要確認】や出典の効き目を、その文の中だけに限定するため。

    制度データは1行が長く（detail や meyasu は数百字）、行末の【要確認】1つで
    行内すべての数字が免除されてしまうのを防ぐ。
    """
    return [seg for seg in SEGMENT_SPLIT.split(text) if seg.strip()]


def interesting(text):
    """制度データの本文らしい行だけを対象にする。"""
    if STYLE_NOISE.search(text):
        return False        # 見た目の指定（CSS）
    if DATA_FIELD.search(text):
        return True         # 制度データの項目行。tel: のように日本語が無い行もある
    return bool(JAPANESE.search(text))


def scan(wide=False):
    """未確認の事実を [(ファイル, 行, 種別, 値, 行本文)] で返す。"""
    log = fact_log_text()
    base, added, removed = diff_lines(wide)
    cache = {}
    findings, seen = [], set()

    def originals(path):
        if path not in cache:
            cache[path] = base_text(base, path)
        return cache[path]

    # ① 新しく書かれた事実で、出典の裏付けが無いもの
    for path, lineno, text in added:
        if not interesting(text):
            continue
        for seg in segments(text):
            if SOURCE_HINT.search(seg):
                continue      # この文には【要確認】か出典が付いている
            for label, pattern in PATTERNS:
              for value in pattern.findall(seg):
                value = value.strip()
                if value in log:              # 台帳で確認済み
                    continue
                if value in originals(path):  # もともと載っていた値（今回の新情報ではない）
                    continue
                key = (path, label, value)
                if key in seen:
                    continue
                seen.add(key)
                findings.append((path, lineno, label, value, seg.strip()[:120]))

    # ② もともと載っていた事実が、現在のファイルから消えているもの（ルールB違反の疑い）
    current = {}
    for path, lineno, text in removed:
        if not interesting(text):
            continue
        for label, pattern in PATTERNS:
            for value in pattern.findall(text):
                value = value.strip()
                if path not in current:
                    current[path] = file_text(path)
                if value in current[path]:    # どこかに残っている＝書き換えではない
                    continue
                key = (path, "削除・変更", value)
                if key in seen:
                    continue
                seen.add(key)
                findings.append((path, lineno, "既存の値の削除・変更",
                                 value, text.strip()[:120]))

    return findings


def format_findings(findings):
    lines = []
    for path, lineno, label, value, text in findings[:40]:
        lines.append(f"  ・{path}:{lineno}  [{label}] 「{value}」")
        lines.append(f"      → {text}")
    if len(findings) > 40:
        lines.append(f"  …ほか {len(findings) - 40} 件")
    return "\n".join(lines)


GUIDE = """
対応の選択肢は3つです（推測で埋めるのは不可）:
  1. 一次情報（稲美町公式・兵庫県・所管省庁）で確認し、docs/fact-log.md に
     「項目 / 値 / 出典URL / 確認日」を追記する
  2. 確認できないなら本文に【要確認】を付ける
  3. その記述自体をやめる
既存の値を出典なしに書き換えていないかも確認してください（CLAUDE.md ルールB）。
"""


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "report"
    payload = {}
    if not sys.stdin.isatty():
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except json.JSONDecodeError:
            payload = {}

    if mode == "report":
        findings = scan(wide=True)
        if not findings:
            print("未確認の事実は見つかりませんでした（追加・変更された行が対象）。")
        else:
            print(f"未確認の事実 {len(findings)} 件:\n" + format_findings(findings))
        return 0

    if mode == "postedit":
        path = (payload.get("tool_input") or {}).get("file_path", "")
        if path and not path.endswith(TARGET_SUFFIX):
            return 0
        findings = scan(wide=False)
        if not findings:
            return 0
        msg = (
            f"【事実チェック】出典が確認できない記述が {len(findings)} 件あります:\n"
            + format_findings(findings)
            + "\n"
            + GUIDE
        )
        print(json.dumps({
            "systemMessage": f"事実チェック: 未確認の記述 {len(findings)} 件",
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": msg,
            },
        }, ensure_ascii=False))
        return 0

    if mode == "stopgate":
        if payload.get("stop_hook_active"):
            return 0          # 二重ブロックによるループを避ける
        findings = scan(wide=True)
        if not findings:
            return 0
        reason = (
            f"作業を終える前に、出典が確認できない記述 {len(findings)} 件を処理してください:\n"
            + format_findings(findings)
            + "\n"
            + GUIDE
            + "\n最後の報告では「✅出典で確認したもの」と「⚠️【要確認】のまま残したもの」を分けて書いてください。"
        )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
