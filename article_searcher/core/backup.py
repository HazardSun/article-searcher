"""
索引备份 / 迁移（功能15）—— 纯 stdlib（zipfile），不引入新依赖

备份内容 = 整个 db_path 目录（含 chromadb/ + tags.json + lexical_index.json +
index_meta.json）打包为单个 .zip，并写入 meta.json（app_version / model /
embedding_dim / created_at / sources）。

恢复：
- 默认 overwrite：先自动备份当前索引为 <db_path>_pre_restore_<ts>.zip（可撤销），
  再清空并解压覆盖；
- merge：按顶层文件覆盖式解压（高级选项）；
- 维度 / 模型校验：与当前引擎 meta 不一致时抛 BackupIncompatible，绝不静默覆盖。
"""

import os
import json
import logging
import shutil
import zipfile
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

APP_VERSION = "2.0"


@dataclass
class BackupMeta:
    app_version: str = APP_VERSION
    model: str = ""
    embedding_dim: int = 0
    created_at: str = ""
    sources: List[dict] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class BackupIncompatible(Exception):
    """备份与当前引擎不兼容（模型 / 维度错配）"""


def _clear_dir(path: str):
    """清空目录内容（保留目录本身）。"""
    for entry in os.listdir(path):
        p = os.path.join(path, entry)
        try:
            if os.path.isfile(p) or os.path.islink(p):
                os.remove(p)
            elif os.path.isdir(p):
                shutil.rmtree(p)
        except OSError as e:
            logger.warning("清理目录项失败 %s: %s", p, e)


def backup_index(db_path: str, out_zip: str, meta: BackupMeta) -> str:
    """将 db_path 目录打包为 .zip（含 meta.json）。返回 zip 路径。"""
    db_path = os.path.abspath(db_path)
    if not os.path.isdir(db_path):
        raise FileNotFoundError(f"索引目录不存在: {db_path}")

    meta_to_write = meta.to_dict() if hasattr(meta, "to_dict") else dict(meta)
    if not meta_to_write.get("created_at"):
        meta_to_write["created_at"] = datetime.now().isoformat(timespec="seconds")

    os.makedirs(os.path.dirname(os.path.abspath(out_zip)), exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("meta.json", json.dumps(meta_to_write, ensure_ascii=False, indent=2))
        for root, _dirs, files in os.walk(db_path):
            for fn in files:
                full = os.path.join(root, fn)
                arcname = os.path.relpath(full, db_path)
                zf.write(full, arcname)
    logger.info("已备份索引到 %s", out_zip)
    return out_zip


def _read_meta(zf: zipfile.ZipFile) -> Dict[str, Any]:
    if "meta.json" not in zf.namelist():
        raise BackupIncompatible("备份文件缺少 meta.json，可能不是有效快照")
    raw = zf.read("meta.json").decode("utf-8")
    return json.loads(raw)


def _as_meta(current) -> Optional[BackupMeta]:
    if current is None:
        return None
    if isinstance(current, BackupMeta):
        return current
    if isinstance(current, dict):
        return BackupMeta(
            app_version=current.get("app_version", APP_VERSION),
            model=current.get("model", ""),
            embedding_dim=current.get("embedding_dim", 0),
            created_at=current.get("created_at", ""),
            sources=current.get("sources", []),
        )
    return None


def restore_index(
    zip_path: str,
    db_path: str,
    mode: str = "overwrite",
    current_meta: Any = None,
) -> Dict[str, Any]:
    """从 .zip 恢复索引。

    - current_meta：当前引擎的 BackupMeta（或 dict），用于校验 model / embedding_dim；
      不一致抛 BackupIncompatible。
    - mode="overwrite"：先自动备份当前为可撤销 zip，再清空覆盖；
      mode="merge"：覆盖式解压（按文件名 upsert）。
    返回 {restored, files, note}。
    """
    zip_path = os.path.abspath(zip_path)
    db_path = os.path.abspath(db_path)
    if not os.path.isfile(zip_path):
        raise FileNotFoundError(f"备份文件不存在: {zip_path}")

    cur = _as_meta(current_meta)

    with zipfile.ZipFile(zip_path) as zf:
        meta_dict = _read_meta(zf)
        if cur is not None:
            if cur.model and meta_dict.get("model") and meta_dict["model"] != cur.model:
                raise BackupIncompatible(
                    f"模型不匹配：备份={meta_dict['model']} 当前={cur.model}。需重建索引。"
                )
            if (cur.embedding_dim and meta_dict.get("embedding_dim")
                    and int(meta_dict["embedding_dim"]) != int(cur.embedding_dim)):
                raise BackupIncompatible(
                    f"向量维度不匹配：备份={meta_dict['embedding_dim']} "
                    f"当前={cur.embedding_dim}。需重建索引。"
                )

        backup_of_current = None
        if mode == "overwrite":
            os.makedirs(db_path, exist_ok=True)
            if os.listdir(db_path):
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                parent = os.path.dirname(db_path) or "."
                base = os.path.basename(db_path.rstrip("/\\")) or "chromadb"
                backup_of_current = os.path.join(parent, f"{base}_pre_restore_{ts}.zip")
                # 用备份内信息回写一份当前快照（可撤销）
                backup_index(
                    db_path, backup_of_current,
                    BackupMeta(
                        app_version=meta_dict.get("app_version", APP_VERSION),
                        model=meta_dict.get("model", ""),
                        embedding_dim=meta_dict.get("embedding_dim", 0),
                        created_at=meta_dict.get("created_at", ""),
                        sources=meta_dict.get("sources", []),
                    ),
                )
            _clear_dir(db_path)
            zf.extractall(db_path)
        elif mode == "merge":
            os.makedirs(db_path, exist_ok=True)
            zf.extractall(db_path)
        else:
            raise ValueError(f"未知恢复模式: {mode}")

        file_count = len([n for n in zf.namelist() if n != "meta.json"])

    note = (f"已自动备份恢复前索引到 {backup_of_current}（可撤销）"
            if backup_of_current else "无当前索引内容可备份")
    return {"restored": True, "files": file_count, "note": note}
