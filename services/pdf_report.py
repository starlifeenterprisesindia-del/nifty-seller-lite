from __future__ import annotations

import json
from datetime import datetime
from html import escape
from io import BytesIO
from typing import Any, Iterable, Sequence

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from analysis.evidence_matrix import build_compact_evidence_matrix
from config import CONFIG
from models import MarketSnapshot


_PAGE_SIZE = landscape(A4)
_DARK = colors.HexColor("#223047")
_BLUE = colors.HexColor("#DCE8F7")
_LIGHT = colors.HexColor("#F4F6F8")
_WARN = colors.HexColor("#FFF2CC")
_RED = colors.HexColor("#FCE8E6")
_GREEN = colors.HexColor("#E6F4EA")
_GRID = colors.HexColor("#AEB7C2")


def _text(value: Any, decimals: int = 2) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "YES" if value else "NO"
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, float):
        if pd.isna(value):
            return "-"
        return f"{value:,.{decimals}f}"
    return str(value)


def _pct(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return str(value)


def _paragraph(value: Any, style: ParagraphStyle) -> Paragraph:
    text = escape(_text(value)).replace("\n", "<br/>")
    return Paragraph(text, style)


def _bullet_lines(values: Iterable[Any]) -> str:
    cleaned = [escape(_text(value)) for value in values if _text(value).strip()]
    return "<br/>".join(f"- {value}" for value in cleaned) if cleaned else "- None"


def _table(
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    widths: Sequence[float] | None = None,
    compact: bool = False,
    header_background: colors.Color = _DARK,
) -> Table:
    styles = _styles()
    body_style = styles["Small"] if compact else styles["Body"]
    header_style = styles["TableHeader"]
    data: list[list[Any]] = [[_paragraph(header, header_style) for header in headers]]
    for row in rows:
        data.append([_paragraph(value, body_style) for value in row])
    table = Table(
        data,
        colWidths=list(widths) if widths else None,
        repeatRows=1,
        hAlign="LEFT",
        splitByRow=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), header_background),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.35, _GRID),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _LIGHT]),
            ]
        )
    )
    return table


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "Title": ParagraphStyle(
            "AuditTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=24,
            alignment=TA_CENTER,
            textColor=_DARK,
            spaceAfter=8,
        ),
        "Subtitle": ParagraphStyle(
            "AuditSubtitle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#465568"),
            spaceAfter=10,
        ),
        "H1": ParagraphStyle(
            "AuditH1",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=16,
            textColor=_DARK,
            spaceBefore=8,
            spaceAfter=5,
            keepWithNext=True,
        ),
        "H2": ParagraphStyle(
            "AuditH2",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=13,
            textColor=_DARK,
            spaceBefore=6,
            spaceAfter=3,
            keepWithNext=True,
        ),
        "Body": ParagraphStyle(
            "AuditBody",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=7.5,
            leading=10,
            alignment=TA_LEFT,
            spaceAfter=3,
        ),
        "Small": ParagraphStyle(
            "AuditSmall",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=6.2,
            leading=8,
            alignment=TA_LEFT,
        ),
        "TableHeader": ParagraphStyle(
            "AuditTableHeader",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=6.6,
            leading=8,
            textColor=colors.white,
            alignment=TA_LEFT,
        ),
        "Callout": ParagraphStyle(
            "AuditCallout",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=12,
            textColor=_DARK,
            spaceAfter=4,
        ),
        "Mono": ParagraphStyle(
            "AuditMono",
            parent=base["Code"],
            fontName="Courier",
            fontSize=5.2,
            leading=6.4,
            leftIndent=0,
            spaceAfter=2,
        ),
    }


def _section_title(text: str) -> Paragraph:
    return Paragraph(escape(text), _styles()["H1"])


def _sub_title(text: str) -> Paragraph:
    return Paragraph(escape(text), _styles()["H2"])


def _callout(text: str, background: colors.Color) -> Table:
    style = _styles()["Callout"]
    table = Table([[_paragraph(text, style)]], colWidths=[257 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), background),
                ("BOX", (0, 0), (-1, -1), 0.6, _GRID),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def _level_row(label: str, level: Any) -> list[Any]:
    if level is None:
        return [label, "-", "-", "-", "UNAVAILABLE", "-"]
    return [
        label,
        f"{level.lower:,.2f} - {level.upper:,.2f}",
        level.midpoint,
        level.distance_points,
        level.status,
        ", ".join(level.sources),
    ]


def _trade_leg_text(legs: Sequence[Any]) -> str:
    if not legs:
        return "-"
    return " + ".join(f"{leg.strike:,.0f} {leg.side} ({leg.role})" for leg in legs)


def _frame_rows(
    frame: pd.DataFrame, columns: Sequence[str], tail: int
) -> list[list[Any]]:
    if frame is None or frame.empty:
        return []
    available = [column for column in columns if column in frame.columns]
    result: list[list[Any]] = []
    for _, row in frame[available].tail(tail).iterrows():
        result.append([row.get(column) for column in available])
    return result


def _page_decor(canvas: Any, document: Any, snapshot: MarketSnapshot) -> None:
    canvas.saveState()
    width, height = _PAGE_SIZE
    canvas.setStrokeColor(_GRID)
    canvas.setLineWidth(0.4)
    canvas.line(15 * mm, height - 12 * mm, width - 15 * mm, height - 12 * mm)
    canvas.setFont("Helvetica-Bold", 7)
    canvas.setFillColor(_DARK)
    canvas.drawString(
        15 * mm, height - 9 * mm, "Nifty Seller Lite - Full Live Audit Report"
    )
    canvas.setFont("Helvetica", 6.5)
    canvas.setFillColor(colors.HexColor("#566273"))
    canvas.drawRightString(
        width - 15 * mm,
        height - 9 * mm,
        f"Snapshot {snapshot.snapshot_id[-12:]} | {snapshot.created_at.strftime('%d-%m-%Y %H:%M:%S IST')}",
    )
    canvas.line(15 * mm, 11 * mm, width - 15 * mm, 11 * mm)
    canvas.drawString(
        15 * mm,
        7 * mm,
        "Decision-support only. Verify broker quotes, liquidity, margin and hedge before trading.",
    )
    canvas.drawRightString(width - 15 * mm, 7 * mm, f"Page {document.page}")
    canvas.restoreState()


def build_full_audit_pdf(snapshot: MarketSnapshot) -> bytes:
    """Render the current authoritative snapshot without recalculating any strategy.

    The report reads the already-built MarketSnapshot. It does not call Dhan, mutate
    state, recalculate the Final One-Brain Decision or create a second prediction path.
    """

    styles = _styles()
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=_PAGE_SIZE,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=17 * mm,
        bottomMargin=15 * mm,
        title=f"Nifty Seller Lite Audit {snapshot.snapshot_id}",
        author="Nifty Seller Lite",
        subject="Live market decision audit",
        pageCompression=1,
    )
    story: list[Any] = []

    # Cover and immutable snapshot identity.
    story.append(Paragraph("Nifty Seller Lite", styles["Title"]))
    story.append(Paragraph("Full Live Audit Report", styles["Subtitle"]))
    snapshot_rows = [
        ["App version", CONFIG.version, "Snapshot ID", snapshot.snapshot_id],
        [
            "Created at",
            snapshot.created_at.isoformat(),
            "Session",
            snapshot.market_session.label,
        ],
        [
            "NIFTY",
            snapshot.nifty_quote.get("last_price"),
            "Expiry",
            snapshot.expiry or "-",
        ],
        [
            "Mode",
            "READ ONLY",
            "Final brain",
            "analysis/decision.py (single canonical brain)",
        ],
    ]
    story.append(
        _table(
            ["Field", "Value", "Field", "Value"],
            snapshot_rows,
            widths=[30 * mm, 92 * mm, 30 * mm, 105 * mm],
        )
    )
    story.append(Spacer(1, 4 * mm))
    session_color = _GREEN if snapshot.market_session.is_live else _WARN
    story.append(
        _callout(
            f"SESSION STATUS: {snapshot.market_session.label} - {snapshot.market_session.message}",
            session_color,
        )
    )
    story.append(Spacer(1, 3 * mm))
    story.append(
        Paragraph(
            "Audit rule: this PDF is a frozen rendering of the same MarketSnapshot shown on screen. "
            "It performs no API request and no independent CE/PE/Condor calculation.",
            styles["Body"],
        )
    )

    # Feed integrity.
    story.append(_section_title("1. Snapshot and Feed Integrity"))
    feed_rows = []
    for name, status in snapshot.feed_status.items():
        feed_rows.append(
            [
                name,
                "YES" if status.ok else "NO",
                status.use_state,
                _text(status.age_seconds, 1),
                status.message,
                status.source,
            ]
        )
    story.append(
        _table(
            ["Feed", "Available", "Use", "Age sec", "Message", "Source"],
            feed_rows,
            widths=[27 * mm, 20 * mm, 22 * mm, 19 * mm, 130 * mm, 39 * mm],
            compact=True,
        )
    )

    # Evidence and decision.
    story.append(_section_title("2. Compact All-Features Evidence"))
    matrix = build_compact_evidence_matrix(snapshot)
    matrix_rows = [
        [
            row["Module"],
            _pct(row["Bullish %"]),
            _pct(row["Bearish %"]),
            _pct(row["Neutral %"]),
            _pct(row["Confidence %"]),
            row["Result"],
        ]
        for row in matrix
    ]
    story.append(
        _table(
            ["Module", "Bullish", "Bearish", "Neutral", "Confidence", "Current result"],
            matrix_rows,
            widths=[34 * mm, 19 * mm, 19 * mm, 19 * mm, 23 * mm, 143 * mm],
        )
    )
    story.append(
        Paragraph(
            "Directional percentages are evidence mix, not profit probability. The compact matrix is display-only.",
            styles["Body"],
        )
    )

    decision = snapshot.decision
    story.append(_section_title("3. Final One-Brain Decision"))
    decision_color = _GREEN if decision.final_action != "WAIT" else _WARN
    story.append(
        _callout(
            f"FINAL ACTION: {decision.final_action} | EXECUTION: {decision.execution_status} | "
            f"SIGNAL: {decision.signal_state} | CONFIDENCE: {decision.decision_confidence:.1f}%",
            decision_color,
        )
    )
    decision_rows = []
    for item in (
        decision.ce_sell,
        decision.pe_sell,
        decision.iron_condor,
        decision.wait_need,
    ):
        decision_rows.append(
            [
                item.name,
                _pct(item.score),
                item.status,
                " | ".join(item.reasons) or "-",
                " | ".join(item.cautions) or "None",
            ]
        )
    story.append(
        _table(
            ["Setup", "Score / need", "Status", "Key evidence", "Cautions"],
            decision_rows,
            widths=[28 * mm, 24 * mm, 25 * mm, 88 * mm, 92 * mm],
            compact=True,
        )
    )
    story.append(
        _table(
            ["Top reasons", "Main blocker", "Instant read", "Hedge required"],
            [
                [
                    _bullet_lines(decision.reasons),
                    decision.blocker,
                    decision.instant_action or decision.final_action,
                    "YES" if decision.hedge_required else "NO",
                ]
            ],
            widths=[100 * mm, 74 * mm, 43 * mm, 40 * mm],
        )
    )

    outlook = decision.outlook
    story.append(_section_title("4. Next 5-15 Minute Conditional Outlook"))
    story.append(
        _table(
            [
                "Bullish path",
                "Range path",
                "Bearish path",
                "Fake-move risk",
                "Risk state",
                "Signal memory",
                "Invalidation",
                "Status",
            ],
            [
                [
                    _pct(outlook.bullish_path_pct),
                    _pct(outlook.range_path_pct),
                    _pct(outlook.bearish_path_pct),
                    _pct(outlook.fake_move_risk),
                    outlook.fake_move_state,
                    outlook.signal_memory,
                    outlook.invalidation_text,
                    outlook.status,
                ]
            ],
            widths=[
                27 * mm,
                25 * mm,
                27 * mm,
                29 * mm,
                25 * mm,
                42 * mm,
                43 * mm,
                39 * mm,
            ],
        )
    )
    story.append(
        Paragraph(
            "Fake-move checks:<br/>" + _bullet_lines(outlook.reasons), styles["Body"]
        )
    )

    # Core market evidence.
    story.append(PageBreak())
    story.append(_section_title("5. Core Market Evidence"))
    core = snapshot.core_evidence
    story.append(
        _table(
            [
                "Bullish index",
                "Bearish index",
                "Range index",
                "Confidence",
                "Market state",
                "Move stage",
                "Status",
            ],
            [
                [
                    core.bullish_score,
                    core.bearish_score,
                    core.range_score,
                    _pct(core.confidence),
                    core.market_state,
                    core.move_stage,
                    core.status,
                ]
            ],
            widths=[31 * mm, 31 * mm, 31 * mm, 28 * mm, 47 * mm, 42 * mm, 47 * mm],
        )
    )
    story.append(
        _table(
            ["Main evidence", "Blockers / cautions"],
            [[_bullet_lines(core.reasons), _bullet_lines(core.blockers)]],
            widths=[128.5 * mm, 128.5 * mm],
        )
    )

    story.append(_sub_title("Price Action - completed candles"))
    pa_rows = []
    for item in (
        snapshot.price_action.three_minute,
        snapshot.price_action.fifteen_minute,
    ):
        pa_rows.append(
            [
                item.timeframe,
                item.as_of,
                item.structure,
                item.event,
                item.move_stage,
                item.last_swing_high,
                item.last_swing_low,
                item.invalidation_level,
                item.atr14,
                f"{item.bullish_score:.1f}/{item.bearish_score:.1f}/{item.range_score:.1f}",
                _pct(item.confidence),
                item.status,
            ]
        )
    story.append(
        _table(
            [
                "TF",
                "As of",
                "Structure",
                "Event",
                "Stage",
                "Swing H",
                "Swing L",
                "Invalidation",
                "ATR14",
                "B/B/R",
                "Conf.",
                "Status",
            ],
            pa_rows,
            widths=[
                12 * mm,
                31 * mm,
                27 * mm,
                43 * mm,
                25 * mm,
                20 * mm,
                20 * mm,
                23 * mm,
                17 * mm,
                24 * mm,
                17 * mm,
                25 * mm,
            ],
            compact=True,
        )
    )
    story.append(
        Paragraph(
            f"Cross-timeframe: {escape(snapshot.price_action.combined_state)} - "
            f"{escape(snapshot.price_action.relationship)} (confidence {snapshot.price_action.confidence:.1f}%).",
            styles["Body"],
        )
    )

    story.append(_sub_title("Support and Resistance"))
    levels = snapshot.levels
    level_rows = [
        _level_row("Immediate support", levels.immediate_support),
        _level_row("Strong support", levels.strong_support),
        _level_row("Immediate resistance", levels.immediate_resistance),
        _level_row("Strong resistance", levels.strong_resistance),
    ]
    story.append(
        _table(
            ["Level", "Zone", "Midpoint", "Distance", "Status", "Sources"],
            level_rows,
            widths=[38 * mm, 43 * mm, 24 * mm, 24 * mm, 37 * mm, 91 * mm],
            compact=True,
        )
    )
    story.append(
        _table(
            [
                "Current position",
                "Upside room",
                "Downside room",
                "PDH",
                "PDL",
                "ORH",
                "ORL",
                "Status",
            ],
            [
                [
                    levels.current_position,
                    levels.upside_room,
                    levels.downside_room,
                    levels.previous_day_high,
                    levels.previous_day_low,
                    levels.opening_range_high,
                    levels.opening_range_low,
                    levels.status,
                ]
            ],
            widths=[
                44 * mm,
                30 * mm,
                31 * mm,
                27 * mm,
                27 * mm,
                27 * mm,
                27 * mm,
                44 * mm,
            ],
            compact=True,
        )
    )

    story.append(_sub_title("NIFTY Futures Volume"))
    volume_rows = []
    for item in (snapshot.volume.three_minute, snapshot.volume.fifteen_minute):
        volume_rows.append(
            [
                item.timeframe,
                item.as_of,
                item.current_volume,
                item.baseline_volume,
                item.relative_volume,
                item.volume_state,
                item.volume_trend,
                item.price_direction,
                item.move_support,
                item.baseline_samples,
                _pct(item.confidence),
                item.status,
            ]
        )
    story.append(
        _table(
            [
                "TF",
                "As of",
                "Current",
                "Baseline",
                "Rel. vol",
                "State",
                "Trend",
                "Price",
                "Support",
                "Samples",
                "Conf.",
                "Status",
            ],
            volume_rows,
            widths=[
                12 * mm,
                31 * mm,
                24 * mm,
                24 * mm,
                20 * mm,
                25 * mm,
                26 * mm,
                24 * mm,
                30 * mm,
                18 * mm,
                18 * mm,
                25 * mm,
            ],
            compact=True,
        )
    )
    story.append(
        Paragraph(
            f"Source: {escape(snapshot.volume.source)} | Overall view: {escape(snapshot.volume.overall_view)} | "
            f"Confidence: {snapshot.volume.confidence:.1f}% | Status: {escape(snapshot.volume.status)}",
            styles["Body"],
        )
    )

    story.append(_sub_title("EMA / MACD / RSI"))
    indicator_rows = []
    for item in (snapshot.indicators.three_minute, snapshot.indicators.fifteen_minute):
        indicator_rows.append(
            [
                item.timeframe,
                item.as_of,
                item.close,
                item.ema20,
                item.ema50,
                item.ema_state,
                item.macd,
                item.macd_signal,
                item.macd_histogram,
                item.macd_state,
                item.rsi14,
                item.rsi_state,
                item.status,
            ]
        )
    story.append(
        _table(
            [
                "TF",
                "As of",
                "Close",
                "EMA20",
                "EMA50",
                "EMA state",
                "MACD",
                "Signal",
                "Hist.",
                "MACD state",
                "RSI14",
                "RSI state",
                "Status",
            ],
            indicator_rows,
            widths=[
                11 * mm,
                31 * mm,
                20 * mm,
                20 * mm,
                20 * mm,
                32 * mm,
                18 * mm,
                18 * mm,
                18 * mm,
                28 * mm,
                18 * mm,
                32 * mm,
                24 * mm,
            ],
            compact=True,
        )
    )

    # Options intelligence.
    story.append(PageBreak())
    story.append(_section_title("6. Options Intelligence"))
    option = snapshot.option_intelligence
    story.append(
        _table(
            [
                "Basis",
                "Snapshots",
                "Bullish",
                "Bearish",
                "Range",
                "Confidence",
                "Bias",
                "Persistence",
                "Status",
            ],
            [
                [
                    option.basis,
                    option.snapshot_count,
                    option.bullish_score,
                    option.bearish_score,
                    option.range_score,
                    _pct(option.confidence),
                    option.market_bias,
                    option.persistence,
                    option.status,
                ]
            ],
            widths=[
                48 * mm,
                22 * mm,
                22 * mm,
                22 * mm,
                22 * mm,
                25 * mm,
                30 * mm,
                38 * mm,
                28 * mm,
            ],
        )
    )
    story.append(
        _table(
            ["Reasons", "Blockers"],
            [[_bullet_lines(option.reasons), _bullet_lines(option.blockers)]],
            widths=[128.5 * mm, 128.5 * mm],
        )
    )

    story.append(_sub_title("OI Walls, Clusters and PCR"))
    wall_rows = []
    for wall in (option.ce_wall, option.pe_wall):
        wall_rows.append(
            [
                wall.side,
                wall.strike,
                wall.oi,
                wall.previous_strike,
                wall.migration_points,
                wall.cluster_center,
                wall.cluster_oi,
                wall.status,
            ]
        )
    story.append(
        _table(
            [
                "Side",
                "Wall",
                "OI",
                "Previous",
                "Migration",
                "Cluster",
                "Cluster OI",
                "Status",
            ],
            wall_rows,
            widths=[
                22 * mm,
                30 * mm,
                32 * mm,
                31 * mm,
                30 * mm,
                31 * mm,
                43 * mm,
                38 * mm,
            ],
            compact=True,
        )
    )
    pcr = option.pcr
    story.append(
        _table(
            [
                "Near-ATM OI PCR",
                "Day addition PCR",
                "Intraday addition PCR",
                "Volume PCR",
                "PCR state",
                "Status",
            ],
            [
                [
                    pcr.near_atm_oi_pcr,
                    pcr.day_addition_pcr,
                    pcr.intraday_addition_pcr,
                    pcr.volume_pcr,
                    pcr.state,
                    pcr.status,
                ]
            ],
            widths=[42 * mm, 42 * mm, 48 * mm, 38 * mm, 53 * mm, 34 * mm],
            compact=True,
        )
    )

    story.append(_sub_title("1m / 3m / 5m Flow Windows"))
    window_rows = [
        [
            item.label,
            item.actual_age_seconds,
            item.ce_oi_delta,
            item.pe_oi_delta,
            item.ce_premium_delta,
            item.pe_premium_delta,
            item.ce_volume_delta,
            item.pe_volume_delta,
            item.bias,
            item.status,
        ]
        for item in option.windows
    ]
    story.append(
        _table(
            [
                "Window",
                "Age sec",
                "CE OI d",
                "PE OI d",
                "CE prem d",
                "PE prem d",
                "CE vol d",
                "PE vol d",
                "Bias",
                "Status",
            ],
            window_rows,
            widths=[
                25 * mm,
                22 * mm,
                26 * mm,
                26 * mm,
                27 * mm,
                27 * mm,
                29 * mm,
                29 * mm,
                24 * mm,
                22 * mm,
            ],
            compact=True,
        )
    )

    story.append(_sub_title("Strike-wise Premium + OI + Volume Classification"))
    flow_headers = [
        "Strike",
        "Side",
        "ATM",
        "LTP",
        "Price d",
        "OI",
        "OI d",
        "Volume",
        "Vol d",
        "Class",
        "Bias",
        "Strength",
        "IV",
    ]
    flow_rows = []
    for row in option.flow_rows:
        flow_rows.append(
            [
                row.get("strike"),
                row.get("side"),
                row.get("is_atm"),
                row.get("last_price"),
                row.get("price_delta"),
                row.get("oi"),
                row.get("oi_delta"),
                row.get("volume"),
                row.get("volume_delta"),
                row.get("classification"),
                row.get("directional_bias"),
                row.get("flow_strength"),
                row.get("implied_volatility"),
            ]
        )
    if flow_rows:
        story.append(
            _table(
                flow_headers,
                flow_rows,
                widths=[
                    20 * mm,
                    12 * mm,
                    13 * mm,
                    18 * mm,
                    20 * mm,
                    24 * mm,
                    22 * mm,
                    24 * mm,
                    21 * mm,
                    34 * mm,
                    22 * mm,
                    24 * mm,
                    16 * mm,
                ],
                compact=True,
            )
        )
    else:
        story.append(Paragraph("Option flow matrix unavailable.", styles["Body"]))

    # Market support and risk.
    story.append(PageBreak())
    story.append(_section_title("7. Top-7, VIX, FII/DII and Event Risk"))
    heavy = snapshot.heavyweights
    story.append(
        _table(
            [
                "Covered weight",
                "Weighted move",
                "Est. index contribution",
                "Advancing",
                "Declining",
                "Unchanged",
                "State",
                "Confidence",
                "Status",
            ],
            [
                [
                    _pct(heavy.covered_weight_pct),
                    _pct(heavy.weighted_move_pct),
                    _pct(heavy.estimated_index_contribution_pct),
                    heavy.advancing,
                    heavy.declining,
                    heavy.unchanged,
                    heavy.state,
                    _pct(heavy.confidence),
                    heavy.status,
                ]
            ],
            widths=[
                31 * mm,
                31 * mm,
                37 * mm,
                23 * mm,
                23 * mm,
                23 * mm,
                42 * mm,
                25 * mm,
                22 * mm,
            ],
            compact=True,
        )
    )
    heavy_rows = [
        [
            row.symbol,
            row.name,
            _pct(row.official_weight_pct),
            row.last_price,
            _pct(row.change_pct),
            _pct(row.index_contribution_pct),
            row.direction,
        ]
        for row in heavy.rows
    ]
    story.append(
        _table(
            ["Symbol", "Name", "Weight", "Last", "Change", "Contribution", "Direction"],
            heavy_rows,
            widths=[26 * mm, 54 * mm, 26 * mm, 34 * mm, 29 * mm, 39 * mm, 49 * mm],
            compact=True,
        )
    )

    vix = snapshot.vix_context
    inst = snapshot.institutional_context
    event = snapshot.event_risk
    story.append(_sub_title("Volatility and Background Context"))
    story.append(
        _table(
            [
                "Context",
                "Latest / value",
                "5-day",
                "10-day",
                "15-day",
                "State / regime",
                "Confidence / status",
                "Note",
            ],
            [
                [
                    "India VIX",
                    vix.last_price,
                    vix.change_pct,
                    "-",
                    "-",
                    f"{vix.regime}; {vix.movement}",
                    vix.status,
                    vix.seller_environment,
                ],
                [
                    "FII cash net",
                    inst.latest_fii_net,
                    inst.fii_5d_net,
                    inst.fii_10d_net,
                    inst.fii_15d_net,
                    inst.state,
                    f"{inst.confidence:.1f}% / {inst.status}",
                    f"Observations {inst.observations}",
                ],
                [
                    "DII cash net",
                    inst.latest_dii_net,
                    inst.dii_5d_net,
                    inst.dii_10d_net,
                    inst.dii_15d_net,
                    inst.state,
                    f"{inst.confidence:.1f}% / {inst.status}",
                    f"As of {inst.as_of_date or '-'}",
                ],
                [
                    "Verified event risk",
                    event.level,
                    "-",
                    "-",
                    "-",
                    "VERIFIED" if event.verified else "NOT VERIFIED",
                    event.status,
                    event.note or "None",
                ],
            ],
            widths=[
                34 * mm,
                30 * mm,
                28 * mm,
                28 * mm,
                28 * mm,
                47 * mm,
                36 * mm,
                26 * mm,
            ],
            compact=True,
        )
    )

    # Plans and guards.
    story.append(
        _section_title("8. Protected Plan, Execution Guard and Position Guardian")
    )
    plan = snapshot.trade_plan
    plan_rows = []
    for setup in (plan.ce_sell, plan.pe_sell, plan.iron_condor):
        breakeven = "-"
        if setup.lower_breakeven is not None and setup.upper_breakeven is not None:
            breakeven = f"{setup.lower_breakeven:,.2f} to {setup.upper_breakeven:,.2f}"
        elif setup.lower_breakeven is not None:
            breakeven = f"Lower {setup.lower_breakeven:,.2f}"
        elif setup.upper_breakeven is not None:
            breakeven = f"Upper {setup.upper_breakeven:,.2f}"
        plan_rows.append(
            [
                setup.name,
                _trade_leg_text(setup.short_legs),
                _trade_leg_text(setup.hedge_legs),
                setup.estimated_credit_points,
                setup.width_points,
                setup.max_risk_points,
                breakeven,
                setup.quality_score,
                setup.status,
                setup.blocker,
            ]
        )
    story.append(
        _table(
            [
                "Setup",
                "Sell legs",
                "Hedge legs",
                "Credit",
                "Width",
                "Max risk",
                "Breakeven",
                "Quality",
                "Status",
                "Blocker",
            ],
            plan_rows,
            widths=[
                23 * mm,
                49 * mm,
                49 * mm,
                19 * mm,
                18 * mm,
                22 * mm,
                36 * mm,
                20 * mm,
                24 * mm,
                37 * mm,
            ],
            compact=True,
        )
    )
    guard = snapshot.execution_guard
    story.append(_sub_title("Execution Guard"))
    story.append(
        _table(
            [
                "Readiness",
                "Setup",
                "Signal",
                "Confirmations",
                "Risk budget",
                "Risk / lot",
                "Allowed lots",
                "Entry window",
                "Exit by",
                "Status",
            ],
            [
                [
                    guard.readiness,
                    guard.selected_setup,
                    guard.signal_state,
                    f"{guard.confirmations}/{guard.required_confirmations}",
                    guard.risk_budget_rupees,
                    guard.risk_per_lot_rupees,
                    guard.allowed_lots,
                    guard.entry_window,
                    guard.forced_exit_time,
                    guard.status,
                ]
            ],
            widths=[
                31 * mm,
                31 * mm,
                37 * mm,
                27 * mm,
                27 * mm,
                27 * mm,
                22 * mm,
                28 * mm,
                25 * mm,
                22 * mm,
            ],
            compact=True,
        )
    )
    story.append(
        _table(
            ["Guard evidence", "Guard blockers", "Spot invalidation"],
            [
                [
                    _bullet_lines(guard.reasons),
                    _bullet_lines(guard.blockers),
                    f"Low {guard.spot_invalidation_low if guard.spot_invalidation_low is not None else '-'} | High {guard.spot_invalidation_high if guard.spot_invalidation_high is not None else '-'}",
                ]
            ],
            widths=[105 * mm, 105 * mm, 47 * mm],
        )
    )

    guardian = snapshot.position_guardian
    story.append(_sub_title("Position Guardian"))
    story.append(
        _table(
            [
                "Status",
                "Instruction",
                "Action",
                "Opened",
                "Lots x size",
                "Entry spot",
                "Current spot",
                "Entry credit",
                "Current debit",
                "P&L pts",
                "P&L Rs",
                "Target progress",
            ],
            [
                [
                    guardian.status,
                    guardian.instruction,
                    guardian.action or "-",
                    guardian.opened_at or "-",
                    f"{guardian.lots} x {guardian.lot_size}",
                    guardian.entry_spot,
                    guardian.current_spot,
                    guardian.entry_credit_points,
                    guardian.current_debit_points,
                    guardian.unrealized_pnl_points,
                    guardian.unrealized_pnl_rupees,
                    _pct(guardian.target_progress_pct),
                ]
            ],
            widths=[
                24 * mm,
                38 * mm,
                26 * mm,
                34 * mm,
                24 * mm,
                22 * mm,
                22 * mm,
                22 * mm,
                23 * mm,
                20 * mm,
                22 * mm,
                24 * mm,
            ],
            compact=True,
        )
    )
    if guardian.legs:
        guardian_rows = [
            [
                leg.role,
                leg.side,
                leg.strike,
                leg.entry_price,
                leg.current_price,
                leg.pnl_contribution_points,
                leg.status,
            ]
            for leg in guardian.legs
        ]
        story.append(
            _table(
                [
                    "Role",
                    "Side",
                    "Strike",
                    "Entry",
                    "Current",
                    "P&L contribution",
                    "Status",
                ],
                guardian_rows,
                widths=[44 * mm, 25 * mm, 35 * mm, 35 * mm, 35 * mm, 42 * mm, 41 * mm],
                compact=True,
            )
        )

    # Raw market appendices.
    story.append(PageBreak())
    story.append(_section_title("9. Raw Market Audit Tables"))
    option_columns = [
        "strike",
        "side",
        "is_atm",
        "last_price",
        "oi",
        "day_oi_change",
        "volume",
        "previous_close_price",
        "day_price_change",
        "implied_volatility",
        "top_bid_price",
        "top_ask_price",
    ]
    option_available = [
        column for column in option_columns if column in snapshot.option_chain.columns
    ]
    option_rows = _frame_rows(snapshot.option_chain, option_available, tail=100)
    story.append(_sub_title("Option Chain - active ATM window"))
    if option_rows:
        widths_map = {
            "strike": 23,
            "side": 12,
            "is_atm": 14,
            "last_price": 20,
            "oi": 24,
            "day_oi_change": 25,
            "volume": 24,
            "previous_close_price": 25,
            "day_price_change": 25,
            "implied_volatility": 21,
            "top_bid_price": 22,
            "top_ask_price": 22,
        }
        widths = [widths_map[column] * mm for column in option_available]
        story.append(_table(option_available, option_rows, widths=widths, compact=True))
    else:
        story.append(Paragraph("Option chain unavailable.", styles["Body"]))

    candle_columns = [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "open_interest",
    ]
    for label, frame, tail in (
        ("NIFTY completed 3-minute candles", snapshot.candles_3m, 20),
        ("NIFTY completed 15-minute candles", snapshot.candles_15m, 12),
        ("NIFTY future 3-minute candles", snapshot.future_candles_3m, 20),
        ("NIFTY future 15-minute candles", snapshot.future_candles_15m, 12),
    ):
        available = [column for column in candle_columns if column in frame.columns]
        rows = _frame_rows(frame, available, tail=tail)
        story.append(_sub_title(label))
        if rows:
            widths = [
                42 * mm if column == "timestamp" else 28 * mm for column in available
            ]
            story.append(_table(available, rows, widths=widths, compact=True))
        else:
            story.append(Paragraph("Unavailable.", styles["Body"]))

    # Verification worksheet and compact JSON audit.
    story.append(PageBreak())
    story.append(_section_title("10. Live Outcome Verification Worksheet"))
    story.append(
        Paragraph(
            "Use the exact snapshot ID and time below to compare NIFTY after 5 and 15 minutes. "
            "Generate another PDF at the later checkpoint so both records remain immutable.",
            styles["Body"],
        )
    )
    baseline_price = snapshot.nifty_quote.get("last_price")
    story.append(
        _table(
            [
                "Checkpoint",
                "Time",
                "NIFTY",
                "App state",
                "Bullish path",
                "Range path",
                "Bearish path",
                "Result / notes",
            ],
            [
                [
                    "Baseline",
                    snapshot.created_at.isoformat(),
                    baseline_price,
                    decision.signal_state,
                    _pct(outlook.bullish_path_pct),
                    _pct(outlook.range_path_pct),
                    _pct(outlook.bearish_path_pct),
                    f"Snapshot {snapshot.snapshot_id}",
                ],
                [
                    "After 5 minutes",
                    "To record",
                    "To record",
                    "Compare with later PDF",
                    "-",
                    "-",
                    "-",
                    "Pending",
                ],
                [
                    "After 15 minutes",
                    "To record",
                    "To record",
                    "Compare with later PDF",
                    "-",
                    "-",
                    "-",
                    "Pending",
                ],
            ],
            widths=[
                27 * mm,
                49 * mm,
                27 * mm,
                47 * mm,
                25 * mm,
                25 * mm,
                27 * mm,
                30 * mm,
            ],
        )
    )
    story.append(
        _table(
            ["Review classification", "Definition"],
            [
                ["CORRECT", "Dominant conditional path occurred without invalidation."],
                [
                    "PARTIAL",
                    "Direction broadly matched but range/fake-move caution was material.",
                ],
                ["WRONG", "Opposite path developed and remained confirmed."],
                [
                    "FAKE MOVE AVOIDED",
                    "Instant signal flipped but memory/fake-move filter kept action at WAIT.",
                ],
            ],
            widths=[55 * mm, 202 * mm],
        )
    )

    story.append(_section_title("11. Canonical Snapshot JSON Summary"))
    summary = snapshot.public_summary()
    # A compact JSON appendix is intentionally limited to the canonical public
    # summary because complete candle and option rows are already printed above.
    json_text = json.dumps(summary, indent=2, ensure_ascii=True, default=str)
    chunks: list[str] = []
    current = ""
    for line in json_text.splitlines():
        safe_line = line if len(line) <= 145 else line[:142] + "..."
        candidate = f"{current}\n{safe_line}" if current else safe_line
        if candidate.count("\n") >= 45:
            chunks.append(current)
            current = safe_line
        else:
            current = candidate
    if current:
        chunks.append(current)
    for index, chunk in enumerate(chunks):
        story.append(
            Paragraph(
                escape(chunk).replace(" ", "&nbsp;").replace("\n", "<br/>"),
                styles["Mono"],
            )
        )
        if index < len(chunks) - 1:
            story.append(Spacer(1, 2 * mm))

    story.append(Spacer(1, 3 * mm))
    story.append(
        _callout(
            "END OF AUDIT - This report is read-only and does not prove future performance. "
            "Use it to compare the app's frozen evidence with later market data.",
            _BLUE,
        )
    )

    document.build(
        story,
        onFirstPage=lambda canvas, doc: _page_decor(canvas, doc, snapshot),
        onLaterPages=lambda canvas, doc: _page_decor(canvas, doc, snapshot),
    )
    return buffer.getvalue()


def audit_pdf_filename(snapshot: MarketSnapshot) -> str:
    timestamp = snapshot.created_at.strftime("%Y%m%d_%H%M%S")
    short_id = snapshot.snapshot_id[-8:].replace("-", "")
    return f"nifty_seller_lite_audit_{timestamp}_{short_id}.pdf"
