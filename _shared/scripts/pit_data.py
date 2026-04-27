# -*- coding: utf-8 -*-
r"""pit_data.py — Point-in-Time data-arkkitehtuuri.

Käyttäjän PDF2 (Realistinen backtestaus) -mandaatin mukaan KAIKKI finance/
crypto-finance/betting-backtestit pitää käyttää PiT-rakennetta jotta
look-ahead bias eliminoidaan.

Avainperiaate:
  Jokaiselle datapisteelle on TALLENNETTU 2 aikaleimaa:
    effective_at  — milloin data koskee
    published_at  — milloin tieto tuli julkisesti markkinoiden saataville

Algoritmi ei saa lukea dataa joista published_at > current_simulated_time.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np
import pandas as pd


@dataclass
class PiTRecord:
    """Yksittäinen PiT-tieuusi: tieto + 2 aikaleimaa + payload."""
    effective_at: datetime
    published_at: datetime
    asset: str
    field: str  # esim. "earnings_eps", "price_close", "index_membership"
    value: Any
    source: str = ""
    revision: int = 0  # jos data revisoidaan, kasvata revisionia

    def to_dict(self) -> dict:
        return {
            "effective_at": self.effective_at.isoformat(),
            "published_at": self.published_at.isoformat(),
            "asset": self.asset,
            "field": self.field,
            "value": self.value,
            "source": self.source,
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PiTRecord":
        return cls(
            effective_at=_parse_dt(d["effective_at"]),
            published_at=_parse_dt(d["published_at"]),
            asset=d["asset"],
            field=d["field"],
            value=d["value"],
            source=d.get("source", ""),
            revision=int(d.get("revision", 0)),
        )


def _parse_dt(s: str) -> datetime:
    if isinstance(s, datetime):
        return s
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


class PiTStore:
    """JSONL-pohjainen Point-in-Time data store."""

    def __init__(self, jsonl_path: Path | str):
        self.path = Path(jsonl_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, rec: PiTRecord):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")

    def query_at(self, simulated_time: datetime,
                  asset: str | None = None,
                  field: str | None = None) -> list[PiTRecord]:
        """Palauta TIEDOT, jotka olivat saatavilla simulated_time-hetkellä.

        Eli rekisterit joiden published_at <= simulated_time.
        Jos asset/field annettu, suodata.
        """
        out: list[PiTRecord] = []
        if not self.path.exists():
            return out
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                rec = PiTRecord.from_dict(d)
                if rec.published_at > simulated_time:
                    continue  # tulevaisuuden tieto — kielletty
                if asset and rec.asset != asset:
                    continue
                if field and rec.field != field:
                    continue
                out.append(rec)
        return out

    def latest_value_at(self, simulated_time: datetime, asset: str,
                         field: str) -> Any:
        """Hae uusin tieto (suurin effective_at) jonka published_at <= sim_time.

        Käytä esim. "mikä oli MSFT:n viim. Q-tulos kun simuloitu päivä on X?"
        """
        recs = self.query_at(simulated_time, asset, field)
        if not recs:
            return None
        # Ota se jonka effective_at on suurin (= uusin tieto)
        # Jos effective_at sama, ota uusin revision
        recs.sort(key=lambda r: (r.effective_at, r.revision), reverse=True)
        return recs[0].value


def assert_no_lookahead(df: pd.DataFrame, time_col: str = "ts",
                         pub_col: str = "published_at",
                         simulated_time_col: str = "simulated_time") -> None:
    """Validointi: rivit joissa published_at > simulated_time ovat kiellettyjä.

    Kutsu tätä jokaisen backtest-iteraation alussa.
    Raise ValueError jos look-ahead havaitaan.
    """
    if pub_col not in df.columns or simulated_time_col not in df.columns:
        return
    pub = pd.to_datetime(df[pub_col], utc=True, errors="coerce")
    sim = pd.to_datetime(df[simulated_time_col], utc=True, errors="coerce")
    leak = df[pub > sim]
    if not leak.empty:
        raise ValueError(f"LOOK-AHEAD BIAS: {len(leak)} riviä joiden "
                          f"published_at > simulated_time. Esim:\n{leak.head(3)}")


# ---------------------------------------------------------------------------
# Survivorship-bias-membership: "mitä yhtiöitä oli S&P 500:ssa milläkin hetkellä"
# ---------------------------------------------------------------------------

class IndexMembership:
    """PiT-historiallinen indeksin jäsenyys.

    Ratkaisee survivorship-bias:n: kun simuloidaan datassa "mitkä yhtiöt olivat
    S&P 500:ssa 2008-09-15", saadaan SE LISTA, EI nykyinen lista.
    """

    def __init__(self, store: PiTStore):
        self.store = store

    def members_at(self, index: str, simulated_time: datetime) -> list[str]:
        """Palauta indeksin jäsenet sim_time-hetkellä."""
        recs = self.store.query_at(simulated_time, asset=index,
                                    field="index_membership")
        if not recs:
            return []
        # Käytä uusinta tietoa
        recs.sort(key=lambda r: r.effective_at, reverse=True)
        latest = recs[0]
        if isinstance(latest.value, list):
            return [str(x) for x in latest.value]
        return []


if __name__ == "__main__":
    import tempfile

    print("=== PiT-store smoke ===")
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tf:
        store = PiTStore(tf.name)

        # Simuloi: AAPL Q1 2024 -tulos (efektiivinen 2024-03-31, julkaistu 2024-04-25)
        store.append(PiTRecord(
            effective_at=datetime(2024, 3, 31, tzinfo=timezone.utc),
            published_at=datetime(2024, 4, 25, tzinfo=timezone.utc),
            asset="AAPL",
            field="earnings_eps",
            value=1.40,
            source="EDGAR",
        ))

        # 2024-04-15 — algoritmi ei vielä näe Q1-tulosta (julkaisematon)
        v = store.latest_value_at(
            datetime(2024, 4, 15, tzinfo=timezone.utc), "AAPL", "earnings_eps")
        print(f"  2024-04-15: AAPL earnings = {v} (pitäisi olla None, ei vielä julkaistu)")

        # 2024-04-25 — algoritmi näkee Q1-tuloksen
        v2 = store.latest_value_at(
            datetime(2024, 4, 26, tzinfo=timezone.utc), "AAPL", "earnings_eps")
        print(f"  2024-04-26: AAPL earnings = {v2} (pitäisi olla 1.40)")

    print("\n=== Index membership ===")
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tf:
        store = PiTStore(tf.name)
        # 2020 jäsenet
        store.append(PiTRecord(
            effective_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
            published_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
            asset="SP500",
            field="index_membership",
            value=["AAPL", "MSFT", "AMZN", "FB"],
        ))
        # 2024 jäsenet (FB poistettu, META lisätty, NVDA lisätty)
        store.append(PiTRecord(
            effective_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            asset="SP500",
            field="index_membership",
            value=["AAPL", "MSFT", "AMZN", "META", "NVDA"],
        ))
        idx = IndexMembership(store)
        m1 = idx.members_at("SP500", datetime(2020, 6, 1, tzinfo=timezone.utc))
        m2 = idx.members_at("SP500", datetime(2024, 6, 1, tzinfo=timezone.utc))
        print(f"  2020-06-01: {m1}")
        print(f"  2024-06-01: {m2}")
