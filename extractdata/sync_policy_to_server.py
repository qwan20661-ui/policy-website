import argparse
import json
import os
import posixpath
import sys
from pathlib import Path

import paramiko

SSH_TIMEOUT = int(os.environ.get("CRAWLER_REMOTE_SSH_TIMEOUT", "15"))
SFTP_TIMEOUT = int(os.environ.get("CRAWLER_REMOTE_SFTP_TIMEOUT", "30"))


def mkdir_p(sftp, remote_dir):
    parts = []
    cur = remote_dir
    while cur and cur != "/":
        parts.append(cur)
        cur = posixpath.dirname(cur)
    for part in reversed(parts):
        try:
            sftp.stat(part)
        except (FileNotFoundError, OSError):
            sftp.mkdir(part)


def upload_file(sftp, local_path, remote_path):
    local = Path(local_path)
    if not local.exists() or not local.is_file():
        return False
    if local.resolve().as_posix() == remote_path:
        return True
    mkdir_p(sftp, posixpath.dirname(remote_path))
    sftp.put(str(local), remote_path)
    return True


def main():
    parser = argparse.ArgumentParser(description="Upload crawled policy files to frontend server.")
    parser.add_argument("--payload-json", required=True, help="JSON payload with local and remote paths.")
    args = parser.parse_args()

    payload = json.loads(args.payload_json)
    host = payload.get("host") or os.environ.get("CRAWLER_REMOTE_HOST")
    port = int(payload.get("port") or os.environ.get("CRAWLER_REMOTE_PORT") or "22")
    user = payload.get("user") or os.environ.get("CRAWLER_REMOTE_USER")
    password = os.environ.get("CRAWLER_REMOTE_PASSWORD")
    if not password:
        raise RuntimeError("CRAWLER_REMOTE_PASSWORD is not configured")

    remote_root = (payload.get("remoteRoot") or "/home/nqiv/demo2/kejiju-frontend/docs1").rstrip("/")
    relative_root = (payload.get("relativeRoot") or "docs1").strip("/\\")
    category = (payload.get("category") or "补充").strip("/\\")
    safe_title = (payload.get("safeTitle") or "policy").strip("/\\")
    markdown_path = payload.get("markdownPath")
    attachment_dir = payload.get("attachmentDir")
    attachments = payload.get("attachments") or []

    remote_policy_dir = posixpath.join(remote_root, category, safe_title)
    remote_attachment_dir = posixpath.join(remote_policy_dir, "attachments")
    relative_policy_dir = posixpath.join(relative_root, category, safe_title)
    relative_attachment_dir = posixpath.join(relative_policy_dir, "attachments")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        port=port,
        username=user,
        password=password,
        timeout=SSH_TIMEOUT,
        auth_timeout=SSH_TIMEOUT,
        banner_timeout=SSH_TIMEOUT,
    )

    uploaded_attachments = []
    try:
        sftp = client.open_sftp()
        sftp.get_channel().settimeout(SFTP_TIMEOUT)
        try:
            mkdir_p(sftp, remote_policy_dir)
            mkdir_p(sftp, remote_attachment_dir)

            md_name = Path(markdown_path).name if markdown_path else "原文.md"
            remote_markdown_path = posixpath.join(remote_policy_dir, md_name)
            relative_markdown_path = posixpath.join(relative_policy_dir, md_name)
            if not markdown_path or not upload_file(sftp, markdown_path, remote_markdown_path):
                raise RuntimeError(f"Markdown file not found: {markdown_path}")

            for item in attachments:
                local_path = item.get("localFilePath") or item.get("filePath")
                status = item.get("status") or "downloaded"
                if not local_path:
                    if status == "downloaded":
                        raise RuntimeError(f"Attachment path is empty: {item.get('name') or item.get('showName')}")
                    continue
                local = Path(local_path)
                if not local.exists():
                    if status == "downloaded":
                        raise RuntimeError(f"Attachment file not found: {local_path}")
                    continue
                name = item.get("name") or item.get("showName") or local.name
                remote_path = posixpath.join(remote_attachment_dir, local.name)
                relative_path = posixpath.join(relative_attachment_dir, local.name)
                if not upload_file(sftp, local, remote_path):
                    raise RuntimeError(f"Attachment upload failed: {local_path}")
                copied = dict(item)
                copied["name"] = name
                copied["showName"] = item.get("showName") or name
                copied["localFilePath"] = str(local).replace("\\", "/")
                copied["filePath"] = relative_path
                copied["remoteFilePath"] = remote_path
                uploaded_attachments.append(copied)
        finally:
            sftp.close()
    finally:
        client.close()

    print(json.dumps({
        "success": True,
        "remotePolicyDir": remote_policy_dir,
        "remoteMarkdownPath": remote_markdown_path,
        "remoteAttachmentDir": remote_attachment_dir,
        "relativePolicyDir": relative_policy_dir,
        "relativeMarkdownPath": relative_markdown_path,
        "relativeAttachmentDir": relative_attachment_dir,
        "attachments": uploaded_attachments,
    }, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"success": False, "message": str(exc)}, ensure_ascii=False))
        sys.exit(1)
