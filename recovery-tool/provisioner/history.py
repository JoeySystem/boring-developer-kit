from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import tempfile


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-") or "device"


class HistoryStore:
    RECORD_KINDS = frozenset({"production", "validation", "unclassified"})
    FIELDS = (
        "timestamp",
        "success",
        "record_kind",
        "release_id",
        "mode",
        "port",
        "chip",
        "mac",
        "flash_size",
        "runtime_port",
        "verification",
        "config_generation",
        "error",
    )

    def __init__(self, root: Path):
        self.root = root

    def _destination(self, receipt: dict) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        mac = _safe_name(receipt.get("chip", {}).get("mac", "unknown"))
        destination = self.root / f"{stamp}-{mac}.json"
        suffix = 1
        while destination.exists():
            destination = self.root / f"{stamp}-{mac}-{suffix}.json"
            suffix += 1
        return destination

    def _atomic_write(self, destination: Path, receipt: dict) -> None:
        encoded = json.dumps(
            receipt,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.root,
            prefix=".receipt-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            stream.write(encoded)
            temporary = Path(stream.name)
        temporary.replace(destination)

    def begin(self, receipt: dict) -> Path:
        destination = self._destination(receipt)
        in_progress = dict(receipt)
        in_progress.update({"status": "in-progress", "success": None})
        self._atomic_write(destination, in_progress)
        return destination

    def finish(self, destination: Path, receipt: dict) -> Path:
        self._atomic_write(destination, receipt)
        self._append_csv(self.root / "history.csv", receipt)
        return destination

    def write(self, receipt: dict) -> Path:
        return self.finish(self._destination(receipt), receipt)

    def list(
        self,
        limit: int | None = None,
        record_kind: str | None = None,
    ) -> list[dict]:
        if record_kind is not None and record_kind not in self.RECORD_KINDS:
            raise ValueError("记录分类无效")
        records = []
        for path in sorted(self.root.glob("*.json"), reverse=True) if self.root.exists() else []:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                kind = record.get("record_kind", "unclassified")
                if kind not in self.RECORD_KINDS:
                    kind = "unclassified"
                if record_kind is not None and kind != record_kind:
                    continue
                record["record_kind"] = kind
                record["record_id"] = path.name
                record["receipt_path"] = str(path)
                records.append(record)
                if limit is not None and len(records) >= limit:
                    break
            except (OSError, json.JSONDecodeError):
                continue
        return records

    def count(self, record_kind: str | None = None) -> int:
        if record_kind is None:
            return sum(1 for _path in self.root.glob("*.json")) if self.root.exists() else 0
        return len(self.list(record_kind=record_kind))

    def counts(self) -> dict[str, int]:
        counts = {kind: 0 for kind in self.RECORD_KINDS}
        for record in self.list():
            counts[record["record_kind"]] += 1
        counts["total"] = sum(counts.values())
        return counts

    def _record_path(self, record_id: str) -> Path:
        if Path(record_id).name != record_id or not record_id.endswith(".json"):
            raise ValueError("记录编号无效")
        path = self.root / record_id
        if not path.is_file():
            raise FileNotFoundError(f"烧录记录不存在：{record_id}")
        return path

    def set_record_kind(self, record_id: str, record_kind: str) -> dict:
        if record_kind not in self.RECORD_KINDS:
            raise ValueError("记录分类无效")
        path = self._record_path(record_id)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("烧录记录内容无效") from exc
        record["record_kind"] = record_kind
        self._atomic_write(path, record)
        self._rewrite_csv()
        record["record_id"] = path.name
        record["receipt_path"] = str(path)
        return record

    def delete_many(self, record_ids: list[str]) -> int:
        unique_ids = list(dict.fromkeys(record_ids))
        paths = [self._record_path(record_id) for record_id in unique_ids]
        if not paths:
            return 0
        trash = self.root / "trash"
        trash.mkdir(parents=True, exist_ok=True)
        for path in paths:
            destination = trash / path.name
            suffix = 1
            while destination.exists():
                destination = trash / f"{path.stem}-{suffix}{path.suffix}"
                suffix += 1
            path.replace(destination)
        self._rewrite_csv()
        return len(paths)

    def clear(self, record_kind: str) -> int:
        if record_kind not in self.RECORD_KINDS:
            raise ValueError("记录分类无效")
        return self.delete_many(
            [record["record_id"] for record in self.list(record_kind=record_kind)]
        )

    @staticmethod
    def _row(record: dict) -> dict:
        chip = record.get("chip", {})
        runtime = record.get("runtime", {})
        return {
            "timestamp": record.get("timestamp", ""),
            "success": record.get("success", ""),
            "record_kind": record.get("record_kind", "unclassified"),
            "release_id": record.get("release_id", ""),
            "mode": record.get("mode", ""),
            "port": record.get("port", ""),
            "chip": chip.get("chip", ""),
            "mac": chip.get("mac", ""),
            "flash_size": chip.get("flash_size", ""),
            "runtime_port": runtime.get("port", ""),
            "verification": record.get("verification", ""),
            "config_generation": runtime.get("config_generation", ""),
            "error": record.get("error", ""),
        }

    def _append_csv(self, destination: Path, record: dict) -> None:
        needs_header = not destination.exists() or destination.stat().st_size == 0
        if not needs_header:
            with destination.open("r", encoding="utf-8", newline="") as stream:
                if next(csv.reader(stream), []) != list(self.FIELDS):
                    self._rewrite_csv()
                    return
        with destination.open("a", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=self.FIELDS)
            if needs_header:
                writer.writeheader()
            writer.writerow(self._row(record))

    def export_csv(self, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=self.FIELDS)
            writer.writeheader()
            for record in reversed(self.list()):
                writer.writerow(self._row(record))
        return destination

    def _rewrite_csv(self) -> None:
        self.export_csv(self.root / "history.csv")
