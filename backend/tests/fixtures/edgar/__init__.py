"""Minimal synthetic SEC companyfacts payload for unit tests.

This avoids checking in real ~1MB EDGAR JSONs. The structure mirrors what
data.sec.gov returns: a top-level `facts.us-gaap.{tag}.units.USD` (or
`shares`) array of records, each with `val`, `start`, `end`, `filed`.
"""
from __future__ import annotations


def _flow_record(start: str, end: str, filed: str, val: float) -> dict:
    return {"start": start, "end": end, "filed": filed, "val": val, "fp": "FY", "form": "10-K"}


def _stock_record(end: str, filed: str, val: float) -> dict:
    return {"end": end, "filed": filed, "val": val, "fp": "FY", "form": "10-K"}


def make_facts() -> dict:
    """A plausible large-cap-ish issuer:
      Revenue 100,000,000 (TTM via 2023-12 annual filed 2024-02)
      OpIncome 25,000,000  (margin 25%)
      NetIncome 20,000,000 (margin 20%)
      OpCF 30,000,000
      Capex 5,000,000      → FCF 25,000,000
      LongTermDebt 40,000,000
      Equity 80,000,000
      Cash 10,000,000      → net debt 30,000,000
      Assets 200,000,000
      Shares 1,000,000

    With spot=$50, mcap=$50M:
      ps_ttm  = 50M / 100M = 0.5
      ev_sales = (50M + 30M) / 100M = 0.8
      ev_ebitda = 80M / (25M+0) = 3.2  (DA = 0 in this synthetic)
      fcf_yield = 25M / 50M = 0.5      → tripped by guard if > 1.0; here 0.5 ok
      roic_ttm = 25M*0.79 / (40M+80M) = 0.1646
      debt_to_equity = 40M / 80M = 0.5
      asset_turnover = 100M / 200M = 0.5
      ni_margin = 0.2, op_margin = 0.25
    """
    return {
        "cik": 1234567,
        "entityName": "Test Issuer",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "label": "Revenues",
                    "units": {
                        "USD": [
                            _flow_record("2022-01-01", "2022-12-31", "2023-02-15", 90_000_000),
                            _flow_record("2023-01-01", "2023-12-31", "2024-02-15", 100_000_000),
                            _flow_record("2024-01-01", "2024-12-31", "2025-02-15", 110_000_000),
                        ],
                    },
                },
                "OperatingIncomeLoss": {
                    "units": {
                        "USD": [
                            _flow_record("2023-01-01", "2023-12-31", "2024-02-15", 25_000_000),
                            _flow_record("2024-01-01", "2024-12-31", "2025-02-15", 28_000_000),
                        ],
                    },
                },
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            _flow_record("2023-01-01", "2023-12-31", "2024-02-15", 20_000_000),
                            _flow_record("2024-01-01", "2024-12-31", "2025-02-15", 22_000_000),
                        ],
                    },
                },
                "NetCashProvidedByUsedInOperatingActivities": {
                    "units": {
                        "USD": [
                            _flow_record("2023-01-01", "2023-12-31", "2024-02-15", 30_000_000),
                        ],
                    },
                },
                "PaymentsToAcquirePropertyPlantAndEquipment": {
                    "units": {
                        "USD": [
                            _flow_record("2023-01-01", "2023-12-31", "2024-02-15", 5_000_000),
                        ],
                    },
                },
                "Assets": {
                    "units": {
                        "USD": [
                            _stock_record("2023-12-31", "2024-02-15", 200_000_000),
                        ],
                    },
                },
                "LongTermDebt": {
                    "units": {
                        "USD": [
                            _stock_record("2023-12-31", "2024-02-15", 40_000_000),
                        ],
                    },
                },
                "CashAndCashEquivalentsAtCarryingValue": {
                    "units": {
                        "USD": [
                            _stock_record("2023-12-31", "2024-02-15", 10_000_000),
                        ],
                    },
                },
                "StockholdersEquity": {
                    "units": {
                        "USD": [
                            _stock_record("2023-12-31", "2024-02-15", 80_000_000),
                        ],
                    },
                },
                "CommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            _stock_record("2023-12-31", "2024-02-15", 1_000_000),
                        ],
                    },
                },
            }
        },
    }


def make_facts_dual_revenue_alias() -> dict:
    """Facts with both `Revenues` (small partial line) and the broader
    `RevenueFromContractWithCustomerExcludingAssessedTax` (consolidated total).

    The extractor must pick the larger one — this is the MSFT/NVDA case.
    """
    facts = make_facts()
    # Override Revenues to be the *smaller* partial line.
    facts["facts"]["us-gaap"]["Revenues"]["units"]["USD"] = [
        _flow_record("2023-01-01", "2023-12-31", "2024-02-15", 5_000_000),
    ]
    # Add the broader alias as the consolidated total.
    facts["facts"]["us-gaap"]["RevenueFromContractWithCustomerExcludingAssessedTax"] = {
        "units": {
            "USD": [
                _flow_record("2023-01-01", "2023-12-31", "2024-02-15", 100_000_000),
            ],
        },
    }
    return facts
