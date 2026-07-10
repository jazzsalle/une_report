"""stdin으로 받은 텍스트를 SessionStart hook의 additionalContext JSON으로 직렬화한다."""
import json
import sys

text = sys.stdin.buffer.read().decode("utf-8", errors="replace")
if len(text) > 9000:
    text = text[:9000] + "\n...(9,000자 초과로 절단됨)"

payload = json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": text,
    }
}, ensure_ascii=False)

# Windows 콘솔 기본 인코딩(cp949)에서 한글/특수문자가 깨지므로 UTF-8로 직접 출력한다.
sys.stdout.buffer.write(payload.encode("utf-8") + b"\n")
