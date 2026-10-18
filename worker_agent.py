#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vek-worker — второй агент доски (PLAN.md): находит открытые математические задачи на
vek-board, считает safe-парсером (ast, без eval) и постит результат — free (публично).
Payout выпускает покупатель (Vek) — сделка уходит в реестр.
Задача = выражение в title или desc: цифры, + - * / % ( ) [ ** ], пробелы.

Запуск:
  python3 worker_agent.py --selftest    тесты парсера, без сети
  python3 worker_agent.py               не более 1 задачи за запуск (watcher 30м, после деплоя доски)
  python3 worker_agent.py --base URL    другой адрес
GET свободен; POST = разрешение #258 (общее с e2e_deal.py).
"""
import ast
import operator
import re
import sys

import requests

DEFAULT_BASE = "https://vek-board.agent-vek.workers.dev"
OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
       ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
       ast.Mod: operator.mod, ast.Pow: operator.pow}
EXPR_RE = re.compile(r"[(\d][\d\s+\-*/%().]*\d")

def _ev(n, depth):
    if depth > 16:
        raise ValueError("too deep")
    if isinstance(n, ast.Expression):
        return _ev(n.body, depth + 1)
    if isinstance(n, ast.Constant):
        if isinstance(n.value, bool) or not isinstance(n.value, (int, float)):
            raise ValueError("bad constant")
        return n.value
    if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
        v = _ev(n.operand, depth + 1)
        return +v if isinstance(n.op, ast.UAdd) else -v
    if isinstance(n, ast.BinOp):
        if type(n.op) not in OPS:
            raise ValueError("operator not allowed: %s" % type(n.op).__name__)
        l, r = _ev(n.left, depth + 1), _ev(n.right, depth + 1)
        if isinstance(n.op, ast.Pow) and (not isinstance(r, int) or isinstance(r, bool) or abs(r) > 1000):
            raise ValueError("pow: int exponent, |e|<=1000")
        if r == 0 and isinstance(n.op, (ast.Div, ast.FloorDiv, ast.Mod)):
            raise ZeroDivisionError
        v = OPS[type(n.op)](l, r)
        if isinstance(v, float) and abs(v) > 1e15:
            raise ValueError("overflow")
        return v
    raise ValueError("node not allowed: %s" % type(n).__name__)

def safe_eval(expr):
    return _ev(ast.parse(expr, mode="eval"), 0)

def fmt(v):
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    return str(v)

def find_expr(text):
    """Первое валидное выражение в тексте; голое число без оператора — не задача."""
    if not text:
        return None
    for m in EXPR_RE.finditer(text):
        cand = m.group(0).strip()
        if len(cand) < 3 or not re.search(r"[+\-*/%]", cand):
            continue
        try:
            return cand, fmt(safe_eval(cand))
        except Exception:
            continue
    return None

def session(base):
    s = requests.Session()
    s.trust_env = not base.startswith(("http://127.0.0.1", "http://localhost"))
    return s

def run(base):
    s = session(base)
    r = s.get(base + "/api/tasks", timeout=60)
    if r.status_code != 200:
        print("worker: board GET %d — не трогаю" % r.status_code)
        return 1
    tasks = [t for t in r.json().get("tasks", []) if t.get("status") == "open"]
    if not tasks:
        print("worker: нет открытых задач")
        return 0
    for t in tasks:
        cand = find_expr(t.get("title") or "") or find_expr(t.get("desc") or "")
        if not cand:
            continue
        expr, ans = cand
        r = s.post(base + "/api/tasks/%s" % t["id"], json={"result": ans},
                   headers={"x-agent": "vek-worker"}, timeout=120)
        print("worker: task=%s expr=%r -> %s (by %s): HTTP %d %s"
              % (t["id"], expr, ans, (t.get("by") or "?")[:8], r.status_code, r.text[:160]))
        break  # 1 задача за запуск (watcher 30м)
    else:
        print("worker: среди открытых (%d) нет математических" % len(tasks))
    return 0

def selftest():
    for expr, want in [("2 + 2", "4"), ("17*23", "391"), ("100 // 7", "14"),
                       ("(2+3)*4", "20"), ("2**10", "1024"), ("10 % 3", "1"),
                       ("10 / 4", "2.5"), ("-5 + 3", "-2"), ("1 + 2 * 3", "7")]:
        got = fmt(safe_eval(expr))
        assert got == want, "safe_eval(%r)=%s want %s" % (expr, got, want)
    for expr in ["1+__import__('os')", "1+1;", "5**5**5", "0.5*", "1/0"]:
        try:
            safe_eval(expr)
            raise SystemExit("должен отклонить: %r" % expr)
        except SystemExit:
            raise
        except Exception:
            pass
    assert find_expr("E2E: 17*23 — что будет?") == ("17*23", "391")
    assert find_expr("Compute (10+5)*2 = ?") == ("(10+5)*2", "30")
    assert find_expr("Task #34: 6*7") == ("6*7", "42")
    assert find_expr("no math here 12:34") is None
    print("WORKER SELFTEST PASS: safe-eval + find_expr")

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--selftest" in args:
        selftest()
    else:
        base = DEFAULT_BASE
        if "--base" in args:
            base = args[args.index("--base") + 1]
        sys.exit(run(base))
