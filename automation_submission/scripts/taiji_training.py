#!/usr/bin/env python3
"""Upload Taiji training files, edit a task, and optionally start it.

The script intentionally does not store credentials. Pass login state through
TAIJI_COOKIE or a curl-style headers file containing a Cookie header.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import ssl
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
import uuid


DEFAULT_BASE_URL = "https://taiji.algo.qq.com"
DEFAULT_BUCKET = "hunyuan-external-1258344706"
DEFAULT_REGION = "ap-guangzhou"
DEFAULT_ROOT = "2026_AMS_ALGO_Competition"
DEFAULT_TEMPLATE_LABEL = ""


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


def api_headers(args: argparse.Namespace) -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Referer": args.base_url.rstrip("/") + "/",
        "User-Agent": "taiji-training-automation/1.0",
    }
    headers.update(parse_header_file(args.headers_file))
    cookie = os.environ.get(args.cookie_env, "").strip()
    if cookie:
        headers["Cookie"] = cookie
    if "Cookie" not in headers:
        raise TaijiError(
            f"missing login cookie: set {args.cookie_env} or pass --headers-file"
        )
    return headers


def read_json_response(req: urllib.request.Request, timeout: int = 120) -> Any:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise TaijiError(f"HTTP {exc.code} for {req.full_url}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise TaijiError(f"request failed for {req.full_url}: {exc}") from exc
    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise TaijiError(f"non-JSON response from {req.full_url}: {body[:500]!r}") from exc


def api_request(
    args: argparse.Namespace,
    method: str,
    path: str,
    payload: Any | None = None,
    timeout: int = 120,
) -> Any:
    url = args.base_url.rstrip("/") + path
    headers = dict(api_headers(args))
    data: bytes | None = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    response = read_json_response(req, timeout=timeout)
    ensure_api_success(response, url)
    return response


def ensure_api_success(response: Any, url: str) -> None:
    if not isinstance(response, dict):
        return
    error = response.get("error")
    if isinstance(error, dict) and error.get("code") not in (None, "SUCCESS"):
        raise TaijiError(f"API error for {url}: {error}")
    if response.get("success") is False:
        raise TaijiError(f"API error for {url}: {response.get('message') or response}")


def response_data(response: Any) -> Any:
    if isinstance(response, dict) and "data" in response:
        return response["data"]
    return response


def extract_task_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("content", "records", "results", "items", "list"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def parse_replace(value: str) -> tuple[str, Path]:
    if "=" in value:
        name, local = value.split("=", 1)
        name = name.strip()
        if not name:
            raise argparse.ArgumentTypeError("--replace name cannot be empty")
        path = Path(local)
    else:
        path = Path(value)
        name = path.name
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"replacement file not found: {path}")
    return name, path


def normalize_sts(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise TaijiError(f"unexpected STS token response: {type(raw).__name__}")
    credentials = raw.get("Credentials") or raw.get("credentials") or {}
    token = {
        "id": raw.get("id") or raw.get("TmpSecretId") or credentials.get("TmpSecretId"),
        "key": raw.get("key")
        or raw.get("TmpSecretKey")
        or credentials.get("TmpSecretKey"),
        "token": raw.get("Token") or credentials.get("Token"),
        "expired_time": raw.get("ExpiredTime") or credentials.get("ExpiredTime"),
    }
    missing = [key for key in ("id", "key", "token", "expired_time") if not token[key]]
    if missing:
        raise TaijiError(f"STS response missing fields: {missing}")
    return token


def quote_cos(value: Any) -> str:
    return urllib.parse.quote(str(value), safe="-_.~")


def canonical_query(params: dict[str, Any]) -> tuple[str, str]:
    items = sorted((key.lower(), value) for key, value in params.items())
    query = "&".join(f"{quote_cos(key)}={quote_cos(value)}" for key, value in items)
    query_list = ";".join(key for key, _ in items)
    return query, query_list


def canonical_headers(headers: dict[str, str]) -> tuple[str, str]:
    items = []
    for key, value in headers.items():
        lowered = key.lower()
        collapsed = " ".join(str(value).strip().split())
        items.append((lowered, quote_cos(collapsed)))
    items.sort()
    header_string = "&".join(f"{quote_cos(key)}={value}" for key, value in items)
    header_list = ";".join(key for key, _ in items)
    return header_string, header_list


def sign_cos_request(
    method: str,
    key: str,
    host: str,
    sts: dict[str, Any],
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
) -> str:
    params = params or {}
    now = int(time.time())
    key_time = f"{now - 60};{int(sts['expired_time'])}"
    signed_headers = {"host": host, **{k.lower(): v for k, v in headers.items()}}
    header_string, header_list = canonical_headers(signed_headers)
    query_string, query_list = canonical_query(params)
    canonical_uri = "/" + key.lstrip("/")
    http_string = (
        f"{method.lower()}\n{canonical_uri}\n{query_string}\n{header_string}\n"
    )
    sign_key = hmac.new(
        str(sts["key"]).encode("utf-8"),
        key_time.encode("utf-8"),
        hashlib.sha1,
    ).hexdigest()
    string_to_sign = (
        "sha1\n"
        f"{key_time}\n"
        f"{hashlib.sha1(http_string.encode('utf-8')).hexdigest()}\n"
    )
    signature = hmac.new(
        sign_key.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).hexdigest()
    return (
        "q-sign-algorithm=sha1"
        f"&q-ak={sts['id']}"
        f"&q-sign-time={key_time}"
        f"&q-key-time={key_time}"
        f"&q-header-list={header_list}"
        f"&q-url-param-list={query_list}"
        f"&q-signature={signature}"
    )


def cos_request(
    method: str,
    args: argparse.Namespace,
    sts: dict[str, Any],
    key: str,
    body: bytes | None = None,
    data_type: str | None = None,
) -> bytes:
    host = f"{args.bucket}.cos.{args.region}.myqcloud.com"
    params: dict[str, Any] = {}
    headers = {"x-cos-security-token": str(sts["token"])}
    if method.upper() == "PUT":
        headers["content-type"] = "application/octet-stream"
    if data_type:
        # The browser SDK uses DataType client-side only. Keep this hook for
        # debugging, but do not send it to COS unless explicitly needed.
        _ = data_type
    auth = sign_cos_request(method, key, host, sts, headers, params=params)
    request_headers = {**headers, "Authorization": auth, "Host": host}
    url = f"https://{host}/{key}"
    req = urllib.request.Request(
        url,
        data=body,
        headers=request_headers,
        method=method.upper(),
    )
    transient_errors = (
        http.client.RemoteDisconnected,
        ssl.SSLError,
        TimeoutError,
        urllib.error.URLError,
    )
    for attempt in range(1, 6):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise TaijiError(f"COS {method} failed for {key}: HTTP {exc.code}: {detail[:500]}") from exc
        except transient_errors as exc:
            if attempt >= 5:
                raise TaijiError(f"COS {method} failed for {key} after {attempt} attempts: {exc}") from exc
            wait_s = min(2 ** attempt, 10)
            print(f"WARN: COS {method} transient error for {key}; retry {attempt}/5 after {wait_s}s: {exc}")
            time.sleep(wait_s)
    raise TaijiError(f"COS {method} failed for {key}: exhausted retries")


def fetch_sts(args: argparse.Namespace) -> dict[str, Any]:
    raw = api_request(
        args,
        "GET",
        "/aide/api/evaluation_tasks/get_federation_token/",
        timeout=60,
    )
    return normalize_sts(response_data(raw))


def get_taiji_user(args: argparse.Namespace) -> str:
    raw = api_request(args, "GET", "/aide/api/app/algo_user/", timeout=60)
    data = response_data(raw)
    user = None
    if isinstance(data, dict):
        user = data.get("user")
    if not user and isinstance(raw, dict):
        user = raw.get("user")
    if not user:
        raise TaijiError(f"could not determine current Taiji user from response: {raw}")
    return str(user)


def default_train_upload_suffix(args: argparse.Namespace) -> str:
    return f"{get_taiji_user(args).strip('/')}/train"


def derive_path_suffix(
    task_detail: dict[str, Any],
    replace_name: str,
    explicit_suffix: str | None,
    root: str,
) -> str:
    if explicit_suffix:
        return explicit_suffix.strip("/")
    files = task_detail.get("trainFiles") or []
    candidates = [
        file_info
        for file_info in files
        if file_info.get("name") == replace_name
    ] + files
    pattern = re.compile(rf"^{re.escape(root)}/(.+)/local--[0-9a-fA-F]+/[^/]+$")
    for file_info in candidates:
        path = str(file_info.get("path", ""))
        if "/common/" in path:
            continue
        match = pattern.match(path)
        if match:
            return match.group(1).strip("/")
    raise TaijiError(
        "could not infer upload path suffix from existing files; pass --path-suffix"
    )


def derive_or_default_path_suffix(
    args: argparse.Namespace,
    task_detail: dict[str, Any],
    replace_name: str,
) -> str:
    try:
        return derive_path_suffix(
            task_detail,
            replace_name,
            args.path_suffix,
            args.competition_root,
        )
    except TaijiError:
        if args.allow_user_train_suffix_fallback:
            return default_train_upload_suffix(args)
        raise


def infer_suffix_from_path(path: str, root: str) -> str | None:
    prefix = root.strip("/") + "/"
    if not path.startswith(prefix):
        return None
    rest = path[len(prefix):].strip("/")
    parts = rest.split("/")
    if len(parts) < 2:
        return None
    # Existing upload paths usually look like:
    # <root>/<suffix>/local--<uuid>/<filename>
    if len(parts) >= 3 and parts[-2].startswith("local--"):
        return "/".join(parts[:-2]).strip("/")
    return "/".join(parts[:-1]).strip("/")


def print_task_files(args: argparse.Namespace) -> None:
    detail = fetch_task_detail(args)
    files = detail.get("trainFiles") or []
    rows = []
    suffixes = []
    for item in files:
        path = str(item.get("path", ""))
        suffix = infer_suffix_from_path(path, args.competition_root)
        if suffix and "/common/" not in path:
            suffixes.append(suffix)
        rows.append({
            "name": item.get("name"),
            "path": path,
            "size": item.get("size"),
            "suggested_path_suffix": suffix,
        })
    print(json.dumps({
        "task": task_summary(detail),
        "trainFiles": rows,
        "suggested_unique_path_suffixes": sorted(set(suffixes)),
    }, ensure_ascii=False, indent=2))


def make_cos_key(args: argparse.Namespace, path_suffix: str, filename: str) -> str:
    return (
        f"{args.competition_root}/{path_suffix.strip('/')}"
        f"/local--{uuid.uuid4().hex}/{filename}"
    )


def upload_replacement(
    args: argparse.Namespace,
    task_detail: dict[str, Any],
    sts: dict[str, Any],
    replace_name: str,
    local_path: Path,
) -> dict[str, Any]:
    path_suffix = derive_or_default_path_suffix(args, task_detail, replace_name)
    key = make_cos_key(args, path_suffix, replace_name)
    body = local_path.read_bytes()
    cos_request("PUT", args, sts, key, body=body)
    remote = cos_request("GET", args, sts, key)
    local_sha1 = hashlib.sha1(body).hexdigest()
    remote_sha1 = hashlib.sha1(remote).hexdigest()
    if local_sha1 != remote_sha1:
        raise TaijiError(f"uploaded file hash mismatch for {replace_name}")
    mtime = dt.datetime.fromtimestamp(local_path.stat().st_mtime).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    return {"name": replace_name, "path": key, "mtime": mtime, "size": len(body)}


def replace_train_files(
    task_detail: dict[str, Any],
    replacements: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    updated = copy.deepcopy(task_detail)
    seen: set[str] = set()
    new_files = []
    for file_info in updated.get("trainFiles") or []:
        name = file_info.get("name")
        if name in replacements:
            new_files.append(replacements[name])
            seen.add(name)
        else:
            keep = {
                key: file_info[key]
                for key in ("name", "path", "mtime", "size")
                if key in file_info
            }
            new_files.append(keep)
    missing = sorted(set(replacements) - seen)
    if missing:
        raise TaijiError(f"task trainFiles did not contain replacement targets: {missing}")
    updated["trainFiles"] = new_files
    return updated


def add_train_files(
    task_detail: dict[str, Any],
    additions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    updated = copy.deepcopy(task_detail)
    files = updated.get("trainFiles") or []
    existing = {str(file_info.get("name")) for file_info in files}
    duplicates = sorted(name for name in additions if name in existing)
    if duplicates:
        raise TaijiError(
            f"trainFiles already contain added targets: {duplicates}; use --replace instead"
        )
    updated["trainFiles"] = [
        {
            key: file_info[key]
            for key in ("name", "path", "mtime", "size")
            if key in file_info
        }
        for file_info in files
    ] + [additions[name] for name in sorted(additions)]
    return updated


def fetch_train_template(args: argparse.Namespace) -> dict[str, Any]:
    query = urllib.parse.urlencode({"label": args.template_label})
    response = api_request(
        args,
        "GET",
        f"/taskmanagement/api/v1/webtasks/external/template?{query}",
        timeout=60,
    )
    data = response_data(response)
    if not isinstance(data, dict) or not isinstance(data.get("trainFiles"), list):
        raise TaijiError(f"unexpected training template response: {response}")
    return data


def print_train_template_files(args: argparse.Namespace) -> None:
    template = fetch_train_template(args)
    files = template.get("trainFiles") or []
    rows = []
    suffixes = []
    for item in files:
        path = str(item.get("path", ""))
        suffix = infer_suffix_from_path(path, args.competition_root)
        if suffix and "/common/" not in path:
            suffixes.append(suffix)
        rows.append({
            "name": item.get("name"),
            "path": path,
            "size": item.get("size"),
            "suggested_path_suffix": suffix,
        })
    print(json.dumps({
        "template": {
            "id": template.get("id"),
            "label": template.get("label"),
            "modelName": template.get("modelName"),
            "trainDataName": template.get("trainDataName"),
        },
        "trainFiles": rows,
        "suggested_unique_path_suffixes": sorted(set(suffixes)),
        "default_upload_path_suffix": default_train_upload_suffix(args),
    }, ensure_ascii=False, indent=2))


def template_payload(args: argparse.Namespace, template: dict[str, Any]) -> dict[str, Any]:
    files = [
        {key: item[key] for key in ("name", "path", "mtime", "size") if key in item}
        for item in template.get("trainFiles") or []
    ]
    return {
        "templateId": template.get("id"),
        "name": args.new_job_name,
        "description": args.new_job_desc,
        "modelName": template.get("modelName") or "",
        "trainDataName": template.get("trainDataName") or "",
        "hostGpuNum": args.host_gpu_num,
        "trainFiles": files,
        "label": args.template_label,
    }


def fetch_task_list(args: argparse.Namespace) -> list[dict[str, Any]]:
    response = api_request(
        args,
        "GET",
        "/taskmanagement/api/v1/webtasks/external/task?page=0&size=100",
        timeout=60,
    )
    return extract_task_items(response_data(response))


def task_display_values(task: dict[str, Any]) -> set[str]:
    keys = (
        "id",
        "taskId",
        "taskID",
        "jobId",
        "jobID",
        "externalId",
        "externalID",
        "name",
        "taskName",
        "jobName",
    )
    return {str(task.get(key)) for key in keys if task.get(key) not in (None, "")}


def task_summary(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "internal_id": task.get("id"),
        "taskId": task.get("taskId") or task.get("taskID"),
        "jobId": task.get("jobId") or task.get("jobID") or task.get("externalId"),
        "name": task.get("name") or task.get("taskName") or task.get("jobName"),
        "status": task.get("status") or task.get("jzStatus"),
        "newInstanceId": task.get("newInstanceId"),
    }


def resolve_task_id(args: argparse.Namespace) -> str:
    cached = getattr(args, "_resolved_task_id", None)
    if cached:
        return str(cached)
    if args.task_id and str(args.task_id).isdigit():
        args._resolved_task_id = str(args.task_id)
        return str(args._resolved_task_id)

    query = args.job_id or args.task_id or args.job_name
    if not query:
        raise TaijiError("pass --task-id <internal numeric id> or --job-id <displayed Job ID>")

    tasks = fetch_task_list(args)
    matches: list[dict[str, Any]] = []
    for task in tasks:
        if str(query) in task_display_values(task):
            matches.append(task)
            continue
        if args.job_name:
            name = task.get("name") or task.get("taskName") or task.get("jobName")
            if str(name) == args.job_name:
                matches.append(task)

    if len(matches) != 1:
        sample = [task_summary(task) for task in tasks[:20]]
        raise TaijiError(
            f"expected exactly one task for {query!r}, got {len(matches)}. "
            f"Run --list-tasks to inspect candidates. sample={sample}"
        )

    internal_id = matches[0].get("id")
    if internal_id is None:
        raise TaijiError(f"matched task does not contain internal numeric id: {matches[0]}")
    args._resolved_task_id = str(internal_id)
    print(f"resolved_task_id={args._resolved_task_id}")
    return str(args._resolved_task_id)


def print_task_list(args: argparse.Namespace) -> None:
    print(json.dumps([task_summary(task) for task in fetch_task_list(args)], ensure_ascii=False, indent=2))


def fetch_task_detail(args: argparse.Namespace) -> dict[str, Any]:
    task_id = resolve_task_id(args)
    response = api_request(
        args,
        "GET",
        f"/taskmanagement/api/v1/webtasks/external/task/{task_id}",
        timeout=60,
    )
    data = response_data(response)
    if not isinstance(data, dict):
        raise TaijiError(f"unexpected task detail response: {type(data).__name__}")
    return data


def edit_task(args: argparse.Namespace, payload: dict[str, Any]) -> dict[str, Any]:
    task_id = resolve_task_id(args)
    response = api_request(
        args,
        "POST",
        f"/taskmanagement/api/v1/webtasks/external/task/{task_id}",
        payload=payload,
        timeout=120,
    )
    return response_data(response)


def scrub_create_payload(payload: dict[str, Any], name: str, desc: str) -> dict[str, Any]:
    created = copy.deepcopy(payload)
    for key in (
        "id",
        "taskId",
        "taskID",
        "jobId",
        "jobID",
        "externalId",
        "externalID",
        "newInstanceId",
        "instanceId",
        "instances",
        "status",
        "jzStatus",
        "createTime",
        "createdTime",
        "updateTime",
        "updatedTime",
        "lastUpdated",
        "lastUpdateTime",
    ):
        created.pop(key, None)
    for key in ("name", "taskName", "jobName"):
        if key in created:
            created[key] = name
    if not any(key in created for key in ("name", "taskName", "jobName")):
        created["name"] = name
    for key in ("description", "desc", "jobDescription", "taskDescription"):
        if key in created:
            created[key] = desc
    if not any(key in created for key in ("description", "desc", "jobDescription", "taskDescription")):
        created["description"] = desc
    return created


def create_task(args: argparse.Namespace, payload: dict[str, Any]) -> dict[str, Any]:
    response = api_request(
        args,
        "POST",
        "/taskmanagement/api/v1/webtasks/external/task",
        payload=payload,
        timeout=120,
    )
    data = response_data(response)
    if not isinstance(data, dict):
        raise TaijiError(f"unexpected create response: {response}")
    return data


def start_task(args: argparse.Namespace, task_detail: dict[str, Any]) -> str:
    task_id = task_detail.get("taskId") or task_detail.get("taskID") or task_detail.get("id")
    if not task_id:
        raise TaijiError("task detail does not contain taskId")
    response = api_request(
        args,
        "POST",
        f"/taskmanagement/api/v1/webtasks/{task_id}/start",
        timeout=120,
    )
    data = response_data(response)
    if not isinstance(data, dict) or not data.get("id"):
        raise TaijiError(f"unexpected start response: {response}")
    return str(data["id"])


def fetch_task_from_list(args: argparse.Namespace, instance_id: str | None = None) -> dict[str, Any] | None:
    tasks = fetch_task_list(args)
    resolved = getattr(args, "_resolved_task_id", None)
    for task in tasks:
        if resolved and str(task.get("id")) == str(resolved):
            return task
        if instance_id and task.get("newInstanceId") == instance_id:
            return task
    return None


def fetch_pod_log(args: argparse.Namespace, instance_id: str) -> list[str]:
    response = api_request(
        args,
        "GET",
        f"/taskmanagement/api/v1/instances/{instance_id}/pod_log",
        timeout=120,
    )
    data = response_data(response)
    if not isinstance(data, list):
        raise TaijiError(f"unexpected pod log response: {type(data).__name__}")
    return [str(line) for line in data]


def save_log(lines: list[str], log_dir: Path, instance_id: str, path: Path | None = None) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    if path is None:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = log_dir / f"{instance_id}-pod-logs-{stamp}.log"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def poll_instance(args: argparse.Namespace, instance_id: str) -> None:
    last_status = ""
    last_line_count = -1
    index = 0
    log_path: Path | None = None
    log_dir = Path(args.save_log_dir) if args.save_log_dir else None
    while True:
        index += 1
        task = fetch_task_from_list(args, instance_id=instance_id) or {}
        status = f"{task.get('status', '?')}/{task.get('jzStatus', '?')}"
        lines = fetch_pod_log(args, instance_id)
        nonempty = sum(1 for line in lines if line)
        changed = status != last_status or len(lines) != last_line_count
        if changed:
            print(
                f"poll={index} instance={instance_id} status={status} "
                f"lines={len(lines)} nonempty={nonempty}",
                flush=True,
            )
            for line in [line for line in lines if line][-8:]:
                print(line, flush=True)
        if log_dir is not None:
            log_path = save_log(lines, log_dir, instance_id, log_path)
        last_status = status
        last_line_count = len(lines)
        if task.get("jzStatus") == "END" or task.get("status") in {
            "SUCCEED",
            "FAILED",
            "KILLED",
            "UNEXPECTED_FINISHED",
        }:
            break
        if args.poll_count > 0 and index >= args.poll_count:
            print(
                f"poll_limit_reached={args.poll_count}; instance may still be running. "
                "Use --poll-count 0 to follow until terminal status.",
                flush=True,
            )
            break
        time.sleep(args.poll_seconds)
    if log_dir is not None:
        path = save_log(fetch_pod_log(args, instance_id), log_dir, instance_id, log_path)
        print(f"saved_log={path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Upload Taiji training files, update trainFiles, and optionally start the task."
    )
    parser.add_argument("--task-id", help="internal numeric task id used by the detail API")
    parser.add_argument("--job-id", help="displayed Job ID, e.g. angel_training_ams_2026_...")
    parser.add_argument("--job-name", help="displayed job name, used only when it is unique")
    parser.add_argument("--list-tasks", action="store_true", help="print task list with internal ids and exit")
    parser.add_argument("--list-task-files", action="store_true", help="print trainFiles for one task and exit")
    parser.add_argument("--list-create-template", action="store_true", help="print the platform default training template and exit")
    parser.add_argument("--create", action="store_true", help="create a new job from the platform default template")
    parser.add_argument("--template-task-id", help="optional: internal numeric task id to clone instead of the default template")
    parser.add_argument("--template-job-id", help="optional: displayed Job ID to clone instead of the default template")
    parser.add_argument("--template-job-name", help="optional: displayed job name to clone instead of the default template")
    parser.add_argument("--template-label", default=DEFAULT_TEMPLATE_LABEL, help="platform training template label; default is empty")
    parser.add_argument("--new-job-name", help="new job name used with --create")
    parser.add_argument("--new-job-desc", help="new job description used with --create")
    parser.add_argument("--host-gpu-num", type=int, default=1, help="training resource count used when creating a new job")
    parser.add_argument(
        "--replace",
        action="append",
        type=parse_replace,
        default=[],
        metavar="NAME=PATH",
        help="replace one trainFiles entry; may be repeated. Without NAME=, basename(PATH) is used.",
    )
    parser.add_argument(
        "--add",
        action="append",
        type=parse_replace,
        default=[],
        metavar="NAME=PATH",
        help="add one new trainFiles entry; may be repeated. Without NAME=, basename(PATH) is used.",
    )
    parser.add_argument("--start", action="store_true", help="start the task after editing")
    parser.add_argument("--dry-run", action="store_true", help="fetch and validate, but do not upload/edit/start")
    parser.add_argument("--poll", action="store_true", help="poll the new instance after --start")
    parser.add_argument("--poll-instance-id", help="poll an existing instance id without editing or starting a task")
    parser.add_argument("--poll-count", type=int, default=0,
                        help="number of polling iterations; 0 means follow until terminal status")
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--save-log-dir", default="data/taiji_logs")
    parser.add_argument("--path-suffix", help="COS path suffix, e.g. ams_xxx/train; inferred by default")
    parser.add_argument("--headers-file", help="curl-style headers file containing Cookie")
    parser.add_argument("--cookie-env", default="TAIJI_COOKIE")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--competition-root", default=DEFAULT_ROOT)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.allow_user_train_suffix_fallback = False
    if args.poll_instance_id:
        try:
            poll_instance(args, args.poll_instance_id)
            return 0
        except TaijiError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    if args.list_tasks:
        try:
            print_task_list(args)
            return 0
        except TaijiError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    if args.list_task_files:
        if not (args.task_id or args.job_id or args.job_name):
            parser.error("--list-task-files requires one of --task-id, --job-id, or --job-name")
        try:
            print_task_files(args)
            return 0
        except TaijiError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    if args.list_create_template:
        try:
            print_train_template_files(args)
            return 0
        except TaijiError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    if args.create:
        if not args.new_job_name:
            parser.error("--create requires --new-job-name")
        if not args.new_job_desc:
            parser.error("--create requires --new-job-desc")
        if args.host_gpu_num <= 0:
            parser.error("--host-gpu-num must be positive")
        if args.template_task_id or args.template_job_id or args.template_job_name:
            args.task_id = args.template_task_id
            args.job_id = args.template_job_id
            args.job_name = args.template_job_name
        else:
            args.allow_user_train_suffix_fallback = True
    elif not (args.task_id or args.job_id or args.job_name):
        parser.error("one of --task-id, --job-id, or --job-name is required")
    replace_names = [name for name, _ in args.replace]
    add_names = [name for name, _ in args.add]
    duplicate_replace_names = sorted({name for name in replace_names if replace_names.count(name) > 1})
    duplicate_add_names = sorted({name for name in add_names if add_names.count(name) > 1})
    overlap_names = sorted(set(replace_names) & set(add_names))
    if duplicate_replace_names:
        parser.error(f"duplicate --replace targets: {duplicate_replace_names}")
    if duplicate_add_names:
        parser.error(f"duplicate --add targets: {duplicate_add_names}")
    if overlap_names:
        parser.error(f"targets cannot be both --replace and --add: {overlap_names}")
    if not args.replace and not args.add and not args.create:
        parser.error("at least one --replace or --add is required when editing an existing job")
    if args.poll and not args.start:
        parser.error("--poll requires --start")

    try:
        if args.create and not (args.task_id or args.job_id or args.job_name):
            template = fetch_train_template(args)
            task_detail = template_payload(args, template)
            print(
                f"template={template.get('id')} "
                f"files={len(task_detail.get('trainFiles') or [])} "
                f"default_upload_suffix={default_train_upload_suffix(args)}"
            )
        else:
            task_detail = fetch_task_detail(args)
            print(
                f"task={resolve_task_id(args)} name={task_detail.get('name')} "
                f"files={len(task_detail.get('trainFiles') or [])}"
            )
        if args.dry_run:
            for name, path in args.replace:
                suffix = derive_or_default_path_suffix(args, task_detail, name)
                print(f"dry_run_replace={name} local={path} path_suffix={suffix}")
            existing_names = {str(file_info.get("name")) for file_info in task_detail.get("trainFiles") or []}
            for name, path in args.add:
                if name in existing_names:
                    raise TaijiError(
                        f"trainFiles already contain added target {name!r}; use --replace instead"
                    )
                suffix = derive_or_default_path_suffix(args, task_detail, name)
                print(f"dry_run_add={name} local={path} path_suffix={suffix}")
            if args.create:
                payload = replace_train_files(task_detail, {}) if args.replace else copy.deepcopy(task_detail)
                if args.add:
                    dry_run_additions = {
                        name: {
                            "name": name,
                            "path": f"<dry-run>/{name}",
                            "mtime": "",
                            "size": path.stat().st_size,
                        }
                        for name, path in args.add
                    }
                    payload = add_train_files(payload, dry_run_additions)
                if args.task_id or args.job_id or args.job_name:
                    payload = scrub_create_payload(
                        payload,
                        args.new_job_name,
                        args.new_job_desc,
                    )
                print(json.dumps({"dry_run_create_payload": payload}, ensure_ascii=False, indent=2))
            return 0

        sts = fetch_sts(args) if args.replace or args.add else None
        replacements = {}
        for name, path in args.replace:
            if sts is None:
                raise TaijiError("internal error: missing STS token for uploads")
            uploaded = upload_replacement(args, task_detail, sts, name, path)
            replacements[name] = uploaded
            print(
                f"uploaded={name} size={uploaded['size']} path={uploaded['path']}",
                flush=True,
            )
        additions = {}
        existing_names = {str(file_info.get("name")) for file_info in task_detail.get("trainFiles") or []}
        for name, path in args.add:
            if name in existing_names:
                raise TaijiError(
                    f"trainFiles already contain added target {name!r}; use --replace instead"
                )
            if sts is None:
                raise TaijiError("internal error: missing STS token for uploads")
            uploaded = upload_replacement(args, task_detail, sts, name, path)
            additions[name] = uploaded
            print(
                f"added_upload={name} size={uploaded['size']} path={uploaded['path']}",
                flush=True,
            )

        payload = replace_train_files(task_detail, replacements) if replacements else copy.deepcopy(task_detail)
        payload = add_train_files(payload, additions) if additions else payload
        if args.create:
            created_payload = (
                scrub_create_payload(payload, args.new_job_name, args.new_job_desc)
                if args.task_id or args.job_id or args.job_name
                else payload
            )
            edited = create_task(args, created_payload)
            edited_files = edited.get("trainFiles") if isinstance(edited, dict) else []
            created_id = edited.get("id") if isinstance(edited, dict) else None
            print(f"created_task={created_id} name={args.new_job_name} files={len(edited_files or [])}")
            if created_id is not None:
                args._resolved_task_id = str(created_id)
        else:
            edited = edit_task(args, payload)
            edited_files = edited.get("trainFiles") if isinstance(edited, dict) else []
            print(f"edited_task={resolve_task_id(args)} files={len(edited_files or [])}")

        if args.start:
            instance_id = start_task(args, edited if isinstance(edited, dict) else payload)
            print(f"started_instance={instance_id}")
            if args.poll:
                poll_instance(args, instance_id)
        return 0
    except TaijiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
