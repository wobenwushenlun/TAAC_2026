#!/usr/bin/env python3
"""Create a Taiji model evaluation task by model name.

Credentials are read from TAIJI_COOKIE or a curl-style headers file. They are
never written by this script.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

import taiji_training as train_api


BASE_URL = "https://taiji.algo.qq.com"


class TaijiError(RuntimeError):
    pass


def parse_header_file(path: str | None) -> dict[str, str]:
    if not path:
        return {}
    headers: dict[str, str] = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip()] = value.strip()
    return headers


def build_headers(args: argparse.Namespace, json_body: bool = False) -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Referer": args.base_url.rstrip("/") + "/",
        "User-Agent": "taiji-evaluation-automation/1.0",
    }
    headers.update(parse_header_file(args.headers_file))
    cookie = os.environ.get(args.cookie_env, "").strip()
    if cookie:
        headers["Cookie"] = cookie
    if "Cookie" not in headers:
        raise TaijiError(
            f"missing login cookie: set {args.cookie_env} or pass --headers-file"
        )
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def request_json(
    args: argparse.Namespace,
    method: str,
    path: str,
    payload: Any | None = None,
    timeout: int = 120,
) -> Any:
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        args.base_url.rstrip("/") + path,
        data=data,
        headers=build_headers(args, json_body=payload is not None),
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise TaijiError(f"HTTP {exc.code} for {path}: {detail[:500]}") from exc
    if not body:
        return None
    result = json.loads(body)
    if isinstance(result, dict):
        error = result.get("error")
        if isinstance(error, dict) and error.get("code") not in (None, "SUCCESS"):
            raise TaijiError(f"API error for {path}: {error}")
        if result.get("success") is False:
            raise TaijiError(f"API error for {path}: {result.get('message')}")
    return result


def get_user(args: argparse.Namespace) -> str:
    result = request_json(args, "GET", "/aide/api/app/algo_user/", timeout=60)
    user = result.get("user") if isinstance(result, dict) else None
    if not user and isinstance(result, dict) and isinstance(result.get("data"), dict):
        user = result["data"].get("user")
    if not user:
        raise TaijiError("could not determine current Taiji user")
    return str(user)


def find_model(args: argparse.Namespace) -> dict[str, Any]:
    query = urllib.parse.urlencode(
        {"page": 1, "page_size": 20, "search": args.model_name}
    )
    result = request_json(args, "GET", f"/aide/api/external/mould/?{query}", timeout=60)
    results = result.get("results") if isinstance(result, dict) else None
    if not isinstance(results, list):
        raise TaijiError(f"unexpected model list response: {result}")
    exact = [item for item in results if item.get("name") == args.model_name]
    if len(exact) != 1:
        names = [item.get("name") for item in results]
        raise TaijiError(
            f"expected exactly one model named {args.model_name!r}, got {names}"
        )
    return exact[0]


def get_template(args: argparse.Namespace) -> dict[str, Any]:
    result = request_json(args, "GET", "/aide/api/evaluation_tasks/get_template/", timeout=60)
    if not isinstance(result, dict) or not isinstance(result.get("inferFiles"), list):
        raise TaijiError(f"unexpected evaluation template response: {result}")
    return result


def print_template_files(args: argparse.Namespace) -> None:
    template = get_template(args)
    rows = []
    suffixes = []
    for item in template["inferFiles"]:
        path = str(item.get("path", ""))
        suffix = train_api.infer_suffix_from_path(path, args.competition_root)
        if suffix and "/common/" not in path:
            suffixes.append(suffix)
        rows.append({
            "name": item.get("name"),
            "path": path,
            "size": item.get("size"),
            "suggested_path_suffix": suffix,
        })
    print(json.dumps({
        "inferFiles": rows,
        "suggested_unique_path_suffixes": sorted(set(suffixes)),
    }, ensure_ascii=False, indent=2))


def template_files(template: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {key: item[key] for key in ("name", "path", "mtime", "size") if key in item}
        for item in template["inferFiles"]
    ]


def build_infer_files(args: argparse.Namespace, template: dict[str, Any]) -> list[dict[str, Any]]:
    if not args.replace:
        return template_files(template)

    template_like = {"trainFiles": template["inferFiles"]}
    replacements: dict[str, dict[str, Any]] = {}
    known_names = {item.get("name") for item in template["inferFiles"]}
    sts = None if args.dry_run else train_api.fetch_sts(args)

    for name, path in args.replace:
        if name not in known_names:
            raise TaijiError(
                f"template inferFiles did not contain replacement target {name!r}; "
                f"known={sorted(known_names)}"
            )
        suffix = train_api.derive_path_suffix(
            template_like,
            name,
            args.path_suffix,
            args.competition_root,
        )
        if args.dry_run:
            print(f"dry_run_replace={name} local={path} path_suffix={suffix}")
            replacements[name] = {"name": name, "path": f"<dry-run>/{name}", "mtime": "", "size": path.stat().st_size}
        else:
            uploaded = train_api.upload_replacement(args, template_like, sts, name, path)
            replacements[name] = uploaded
            print(f"uploaded={name} size={uploaded['size']} path={uploaded['path']}", flush=True)

    files = []
    seen: set[str] = set()
    for item in template_files(template):
        name = item.get("name")
        if name in replacements:
            files.append(replacements[name])
            seen.add(str(name))
        else:
            files.append(item)
    missing = sorted(set(replacements) - seen)
    if missing:
        raise TaijiError(f"failed to replace inferFiles: {missing}")
    return files


def create_eval(args: argparse.Namespace, model: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
    name = args.eval_name or f"{args.model_name}_eval_{int(time.time() * 1000)}"
    payload = {
        "mould_id": model["id"],
        "name": name,
        "image_name": args.image_name,
        "creator": get_user(args),
        "files": build_infer_files(args, template),
    }
    if args.dry_run:
        print(json.dumps({"dry_run_payload": payload}, ensure_ascii=False, indent=2))
        return {}
    return request_json(args, "POST", "/aide/api/evaluation_tasks/", payload=payload)


def poll_eval(args: argparse.Namespace, eval_id: int) -> None:
    last = ""
    for index in range(1, args.poll_count + 1):
        detail = request_json(args, "GET", f"/aide/api/evaluation_tasks/{eval_id}/", timeout=60)
        status = "/".join(
            str(value)
            for value in (detail.get("status"), detail.get("inner_status"))
            if value
        )
        score = detail.get("score")
        if status != last or score is not None:
            print(f"poll={index} eval_id={eval_id} status={status} score={score}")
        last = status
        if detail.get("status") in {"success", "succeed", "failed", "stopped", "stoped"}:
            break
        if index != args.poll_count:
            time.sleep(args.poll_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a Taiji model evaluation task.")
    parser.add_argument("--model-name", required=not ("--list-template-files" in sys.argv))
    parser.add_argument("--eval-name")
    parser.add_argument("--image-name", default="")
    parser.add_argument("--list-template-files", action="store_true", help="print evaluation inferFiles and exit")
    parser.add_argument(
        "--replace",
        action="append",
        type=train_api.parse_replace,
        default=[],
        metavar="NAME=PATH",
        help="replace one inferFiles entry; may be repeated",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--poll", action="store_true")
    parser.add_argument("--poll-count", type=int, default=20)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--path-suffix", help="COS path suffix; inferred by default")
    parser.add_argument("--headers-file")
    parser.add_argument("--cookie-env", default="TAIJI_COOKIE")
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--bucket", default=train_api.DEFAULT_BUCKET)
    parser.add_argument("--region", default=train_api.DEFAULT_REGION)
    parser.add_argument("--competition-root", default=train_api.DEFAULT_ROOT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.list_template_files:
            print_template_files(args)
            return 0
        model = find_model(args)
        template = get_template(args)
        print(f"model={model['name']} mould_id={model['id']}")
        result = create_eval(args, model, template)
        if not args.dry_run:
            print(
                f"created_eval={result.get('id')} name={result.get('name')} "
                f"status={result.get('status')}"
            )
            if args.poll:
                poll_eval(args, int(result["id"]))
        return 0
    except (TaijiError, train_api.TaijiError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
