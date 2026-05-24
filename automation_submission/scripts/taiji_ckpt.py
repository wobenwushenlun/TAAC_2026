#!/usr/bin/env python3
"""Inspect and publish Taiji training checkpoints.

Taiji creates checkpoint rows from files written by the training job under the
platform checkpoint directory. Publishing a checkpoint promotes it to Model
Management so it can be evaluated.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


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


def headers(args: argparse.Namespace, json_body: bool = False) -> dict[str, str]:
    result = {
        "Accept": "application/json, text/plain, */*",
        "Referer": args.base_url.rstrip("/") + "/",
        "User-Agent": "taiji-ckpt-automation/1.0",
    }
    result.update(parse_header_file(args.headers_file))
    cookie = os.environ.get(args.cookie_env, "").strip()
    if cookie:
        result["Cookie"] = cookie
    if "Cookie" not in result:
        raise TaijiError(
            f"missing login cookie: set {args.cookie_env} or pass --headers-file"
        )
    if json_body:
        result["Content-Type"] = "application/json"
    return result


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
        headers=headers(args, json_body=payload is not None),
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise TaijiError(f"HTTP {exc.code} for {path}: {detail[:500]}") from exc
    result = json.loads(body) if body else None
    if isinstance(result, dict):
        error = result.get("error")
        if isinstance(error, dict) and error.get("code") not in (None, "SUCCESS"):
            raise TaijiError(f"API error for {path}: {error}")
        if result.get("success") is False:
            raise TaijiError(f"API error for {path}: {result.get('message')}")
    return result


def data_or_self(result: Any) -> Any:
    if isinstance(result, dict) and "data" in result:
        return result["data"]
    return result


def list_ckpts(args: argparse.Namespace) -> list[dict[str, Any]]:
    result = request_json(
        args,
        "GET",
        f"/taskmanagement/api/v1/instances/external/{args.instance_id}/get_ckpt",
    )
    data = data_or_self(result)
    if not isinstance(data, list):
        raise TaijiError(f"unexpected ckpt response: {result}")
    return data


def release_ckpt(args: argparse.Namespace) -> Any:
    payload = {"ckpt": args.ckpt, "name": args.name, "desc": args.desc}
    return request_json(
        args,
        "POST",
        f"/taskmanagement/api/v1/instances/external/{args.instance_id}/release_ckpt",
        payload=payload,
    )


def delete_ckpt(args: argparse.Namespace) -> Any:
    payload = {"ckpt": args.ckpt}
    return request_json(
        args,
        "POST",
        f"/taskmanagement/api/v1/instances/external/{args.instance_id}/del_ckpt",
        payload=payload,
    )


def list_models(args: argparse.Namespace) -> Any:
    query = {"page": args.page, "page_size": args.page_size}
    if args.search:
        query["search"] = args.search
    path = "/aide/api/external/mould/?" + urllib.parse.urlencode(query)
    return request_json(args, "GET", path)


def edit_model(args: argparse.Namespace) -> Any:
    payload = {"name": args.name, "desc": args.desc}
    return request_json(args, "PUT", f"/aide/api/external/mould/{args.model_id}/", payload)


def delete_model(args: argparse.Namespace) -> Any:
    return request_json(
        args,
        "POST",
        "/aide/api/external/mould/delete/",
        payload={"id": args.model_id},
    )


def tf_events(args: argparse.Namespace) -> Any:
    return request_json(
        args,
        "GET",
        f"/taskmanagement/api/v1/instances/external/{args.instance_id}/tf_events",
    )


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--headers-file")
    parser.add_argument("--cookie-env", default="TAIJI_COOKIE")
    parser.add_argument("--base-url", default=BASE_URL)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect and publish Taiji checkpoints.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list-ckpt")
    p.add_argument("--instance-id", required=True)
    add_common(p)

    p = sub.add_parser("release-ckpt")
    p.add_argument("--instance-id", required=True)
    p.add_argument("--ckpt", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--desc", required=True)
    add_common(p)

    p = sub.add_parser("delete-ckpt")
    p.add_argument("--instance-id", required=True)
    p.add_argument("--ckpt", required=True)
    add_common(p)

    p = sub.add_parser("tf-events")
    p.add_argument("--instance-id", required=True)
    add_common(p)

    p = sub.add_parser("list-models")
    p.add_argument("--search")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    add_common(p)

    p = sub.add_parser("edit-model")
    p.add_argument("--model-id", type=int, required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--desc", required=True)
    add_common(p)

    p = sub.add_parser("delete-model")
    p.add_argument("--model-id", type=int, required=True)
    add_common(p)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "list-ckpt":
            result = list_ckpts(args)
        elif args.command == "release-ckpt":
            result = release_ckpt(args)
        elif args.command == "delete-ckpt":
            result = delete_ckpt(args)
        elif args.command == "tf-events":
            result = tf_events(args)
        elif args.command == "list-models":
            result = list_models(args)
        elif args.command == "edit-model":
            result = edit_model(args)
        elif args.command == "delete-model":
            result = delete_model(args)
        else:
            raise TaijiError(f"unknown command: {args.command}")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except TaijiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
