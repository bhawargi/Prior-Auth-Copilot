"""Synthetic EHR and payer-policy data access."""
import json
import re
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"


@lru_cache(maxsize=None)
def _patients() -> dict:
    raw = json.loads((DATA_DIR / "patients.json").read_text())
    return {p["id"]: p for p in raw["patients"]}


@lru_cache(maxsize=None)
def _payers() -> dict:
    return json.loads((DATA_DIR / "payers.json").read_text())


def list_patients() -> list:
    return list(_patients().values())


def get_patient(patient_id: str) -> dict:
    return _patients()[patient_id]


def get_document(patient_id: str, doc_id: str):
    for doc in get_patient(patient_id)["documents"]:
        if doc["doc_id"] == doc_id:
            return doc
    return None


def get_payer(payer_id: str) -> dict:
    return _payers()["payers"][payer_id]


def get_policy(policy_id: str):
    return _payers()["policies"].get(policy_id)


def sentences(text: str) -> list:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()
