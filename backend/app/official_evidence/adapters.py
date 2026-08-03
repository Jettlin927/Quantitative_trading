from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Protocol
from urllib.parse import quote, unquote, urlencode, urlsplit

from .contracts import (
    Availability,
    BeaDataQuery,
    BlsSeriesQuery,
    CensusDataQuery,
    EvidenceFetchContext,
    FederalReserveSeriesQuery,
    IssuerIrDocumentQuery,
    OfficialDocument,
    OfficialEvidenceBatch,
    OfficialEvent,
    OfficialFact,
    SecCompanyFactsQuery,
    SecSubmissionsQuery,
    TreasuryFiscalDataQuery,
    TransportRequest,
    TransportResponse,
)


class OfficialEvidenceTransport(Protocol):
    def send(self, request: TransportRequest) -> TransportResponse: ...


class EvidenceAdapterError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class SecSubmissionsAdapter:
    _BASE_URL = "https://data.sec.gov/submissions"

    def __init__(self, *, transport: OfficialEvidenceTransport, user_agent: str):
        if not user_agent.strip():
            raise ValueError("SEC User-Agent 不能为空")
        self._transport = transport
        self._user_agent = user_agent

    def fetch(
        self,
        query: SecSubmissionsQuery,
        *,
        context: EvidenceFetchContext,
    ) -> OfficialEvidenceBatch:
        cik = _normalize_cik(query.cik)
        url = f"{self._BASE_URL}/CIK{quote(cik, safe='')}.json"
        response = self._transport.send(
            TransportRequest(
                method="GET",
                url=url,
                headers=(
                    ("Accept", "application/json"),
                    ("User-Agent", self._user_agent),
                ),
            )
        )
        _require_response(response, expected_url=url)
        payload = _parse_json(response.body)
        recent = payload["filings"]["recent"]
        digest = sha256(response.body).hexdigest()
        documents: list[OfficialDocument] = []
        events: list[OfficialEvent] = []
        for index, accession in enumerate(recent.get("accessionNumber", [])):
            published_at = _parse_datetime(recent["acceptanceDateTime"][index])
            filing_date = _parse_date_start(recent["filingDate"][index])
            primary_document = recent["primaryDocument"][index]
            accession_path = accession.replace("-", "")
            canonical_url = (
                "https://www.sec.gov/Archives/edgar/data/"
                f"{int(cik)}/{accession_path}/{quote(primary_document, safe='')}"
            )
            identity = f"sec:accession:{accession}"
            document = OfficialDocument(
                identity=identity,
                source="sec",
                dataset="edgar_submissions",
                document_type=recent["form"][index],
                source_record_id=accession,
                canonical_url=canonical_url,
                artifact_sha256=digest,
                published_at=published_at,
                effective_at=filing_date,
                reference_period=recent["reportDate"][index] or None,
                available_from=published_at,
                fetched_at=context.fetched_at,
                vintage=published_at.isoformat(),
                revision=accession,
                authorization=context.authorization,
                qualification=context.qualification,
                health=context.health,
                fallback_identity=context.fallback_identity,
            )
            documents.append(document)
            events.append(
                OfficialEvent(
                    identity=f"{identity}:event",
                    source="sec",
                    event_type="sec_filing",
                    evidence_identity=identity,
                    occurred_at=published_at,
                    available_from=published_at,
                    fetched_at=context.fetched_at,
                    authorization=context.authorization,
                    qualification=context.qualification,
                    health=context.health,
                )
            )
        return OfficialEvidenceBatch(
            availability=Availability.AVAILABLE,
            health=context.health,
            documents=tuple(documents),
            events=tuple(events),
        )


class SecCompanyFactsAdapter:
    _BASE_URL = "https://data.sec.gov/api/xbrl/companyfacts"

    def __init__(self, *, transport: OfficialEvidenceTransport, user_agent: str):
        if not user_agent.strip():
            raise ValueError("SEC User-Agent 不能为空")
        self._transport = transport
        self._user_agent = user_agent

    def fetch(
        self,
        query: SecCompanyFactsQuery,
        *,
        context: EvidenceFetchContext,
    ) -> OfficialEvidenceBatch:
        cik = _normalize_cik(query.cik)
        url = f"{self._BASE_URL}/CIK{quote(cik, safe='')}.json"
        response = self._transport.send(
            TransportRequest(
                method="GET",
                url=url,
                headers=(
                    ("Accept", "application/json"),
                    ("User-Agent", self._user_agent),
                ),
            )
        )
        _require_response(response, expected_url=url)
        payload = _parse_json(response.body)
        digest = sha256(response.body).hexdigest()
        entries = (
            payload.get("facts", {})
            .get(query.taxonomy, {})
            .get(query.tag, {})
            .get("units", {})
            .get(query.unit, [])
        )
        availability_by_accession = {
            item.accession: item.available_from
            for item in query.accession_availability
        }
        facts = []
        for item in entries:
            period = str(item["end"])
            context_value = str(
                item.get("frame")
                or f"FY{item.get('fy', 'unknown')}:{item.get('fp', 'unknown')}:{item.get('form', 'unknown')}"
            )
            accession = str(item["accn"])
            published_at = availability_by_accession.get(accession)
            if published_at is None:
                if query.only_mapped_accessions:
                    continue
                raise EvidenceAdapterError(
                    "accession_availability_missing",
                    "SEC company fact 缺少 accession 的精确可得时间",
                )
            logical_identity = f"sec:{query.tag}:{period}:{context_value}"
            facts.append(
                OfficialFact(
                    identity=f"{logical_identity}:{accession}",
                    logical_identity=logical_identity,
                    source="sec",
                    dataset=f"companyfacts:{query.taxonomy}",
                    series_id=query.tag,
                    reference_period=period,
                    context=context_value,
                    value=str(item["val"]),
                    unit=query.unit,
                    source_record_id=(
                        f"{accession}:{query.taxonomy}:{query.tag}:{query.unit}:"
                        f"{period}:{context_value}"
                    ),
                    canonical_url=url,
                    artifact_sha256=digest,
                    published_at=published_at,
                    effective_at=_parse_date_start(period),
                    available_from=published_at,
                    fetched_at=context.fetched_at,
                    vintage=published_at.isoformat(),
                    revision=accession,
                    authorization=context.authorization,
                    qualification=context.qualification,
                    health=context.health,
                    fallback_identity=context.fallback_identity,
                )
            )
        return _fact_batch(facts, context, event_type="sec_fact_filed")


class IssuerIrAdapter:
    def __init__(
        self,
        *,
        transport: OfficialEvidenceTransport,
        allowed_origins: dict[str, str],
    ):
        if not allowed_origins:
            raise ValueError("发行人 IR allowlist 不能为空")
        if any(not _valid_allowed_ir_url(url) for url in allowed_origins.values()):
            raise ValueError("发行人 IR allowlist 只接受 HTTPS URL")
        self._transport = transport
        self._allowed_origins = dict(allowed_origins)

    def fetch(
        self,
        query: IssuerIrDocumentQuery,
        *,
        context: EvidenceFetchContext,
    ) -> OfficialEvidenceBatch:
        allowed = self._allowed_origins.get(query.issuer_id)
        if allowed is None or not _url_matches_allowlist(query.document_url, allowed):
            raise ValueError("发行人 IR URL 不在 allowlist")
        response = self._transport.send(
            TransportRequest(
                method="GET",
                url=query.document_url,
                headers=(("Accept", "text/html,application/pdf"),),
            )
        )
        _require_response(response, expected_url=query.document_url)
        digest = sha256(response.body).hexdigest()
        identity_suffix = sha256(
            f"{query.document_url}|{query.revision}".encode("utf-8")
        ).hexdigest()
        identity = f"issuer_ir:document:{identity_suffix}"
        document = OfficialDocument(
            identity=identity,
            source="issuer_ir",
            dataset=query.issuer_id,
            document_type=query.document_type,
            source_record_id=query.document_url,
            canonical_url=query.document_url,
            artifact_sha256=digest,
            published_at=query.published_at,
            effective_at=query.effective_at,
            reference_period=query.reference_period,
            available_from=query.available_from,
            fetched_at=context.fetched_at,
            vintage=query.vintage,
            revision=query.revision,
            authorization=context.authorization,
            qualification=context.qualification,
            health=context.health,
            fallback_identity=context.fallback_identity,
        )
        event = OfficialEvent(
            identity=f"{identity}:event",
            source="issuer_ir",
            event_type="issuer_release",
            evidence_identity=identity,
            occurred_at=query.effective_at or query.published_at,
            available_from=query.available_from,
            fetched_at=context.fetched_at,
            authorization=context.authorization,
            qualification=context.qualification,
            health=context.health,
        )
        return OfficialEvidenceBatch(
            availability=Availability.AVAILABLE,
            health=context.health,
            documents=(document,),
            events=(event,),
        )


class BlsSeriesAdapter:
    _URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

    def __init__(self, *, transport: OfficialEvidenceTransport):
        self._transport = transport

    def fetch(
        self,
        query: BlsSeriesQuery,
        *,
        context: EvidenceFetchContext,
    ) -> OfficialEvidenceBatch:
        request_body = json.dumps(
            {
                "seriesid": [query.series_id],
                "startyear": str(query.start_year),
                "endyear": str(query.end_year),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        response = self._transport.send(
            TransportRequest(
                method="POST",
                url=self._URL,
                headers=(
                    ("Accept", "application/json"),
                    ("Content-Type", "application/json"),
                ),
                body=request_body,
            )
        )
        _require_response(response, expected_url=self._URL)
        payload = _parse_json(response.body)
        if payload.get("status") != "REQUEST_SUCCEEDED":
            raise EvidenceAdapterError("source_payload_invalid", "BLS 返回非成功状态")
        digest = sha256(response.body).hexdigest()
        facts: list[OfficialFact] = []
        for series in payload.get("Results", {}).get("series", []):
            if series.get("seriesID") != query.series_id:
                continue
            for item in series.get("data", []):
                period = f"{item['year']}-{item['period']}"
                facts.append(
                    _macro_fact(
                        source="bls",
                        dataset="public_api_v2_timeseries",
                        series_id=query.series_id,
                        period=period,
                        context_value="all",
                        value=str(item["value"]),
                        unit=None,
                        source_record_id=f"{query.series_id}:{period}",
                        canonical_url=self._URL,
                        artifact_sha256=digest,
                        release=query.release,
                        context=context,
                    )
                )
        return _fact_batch(facts, context)


class BeaDataAdapter:
    _URL = "https://apps.bea.gov/api/data"

    def __init__(self, *, transport: OfficialEvidenceTransport):
        self._transport = transport

    def fetch(
        self, query: BeaDataQuery, *, context: EvidenceFetchContext
    ) -> OfficialEvidenceBatch:
        url = (
            f"{self._URL}?datasetname={quote(query.dataset_name, safe='')}"
            f"&method=GetData&TableName={quote(query.table_name, safe='')}"
            f"&Year={quote(query.year, safe='')}"
        )
        response = _get_json(self._transport, url)
        digest = sha256(response.body).hexdigest()
        facts = []
        for item in _parse_json(response.body).get("BEAAPI", {}).get("Results", {}).get("Data", []):
            series_id = f"{item['TableName']}.{item['LineNumber']}"
            facts.append(
                _macro_fact(
                    source="bea",
                    dataset=query.dataset_name,
                    series_id=series_id,
                    period=item["TimePeriod"],
                    context_value="all",
                    value=str(item["DataValue"]),
                    unit=item.get("CL_UNIT"),
                    source_record_id=f"{series_id}:{item['TimePeriod']}",
                    canonical_url=url,
                    artifact_sha256=digest,
                    release=query.release,
                    context=context,
                )
            )
        return _fact_batch(facts, context)


class FederalReserveSeriesAdapter:
    _URL = "https://api.stlouisfed.org/fred/series/observations"

    def __init__(self, *, transport: OfficialEvidenceTransport):
        self._transport = transport

    def fetch(
        self,
        query: FederalReserveSeriesQuery,
        *,
        context: EvidenceFetchContext,
    ) -> OfficialEvidenceBatch:
        params = (
            ("file_type", "json"),
            ("observation_end", query.end_date),
            ("observation_start", query.start_date),
            ("series_id", query.series_id),
        )
        url = f"{self._URL}?{urlencode(params)}"
        response = _get_json(self._transport, url)
        digest = sha256(response.body).hexdigest()
        facts = [
            _macro_fact(
                source="federal_reserve",
                dataset="fred_series_observations",
                series_id=query.series_id,
                period=item["date"],
                context_value="all",
                value=str(item["value"]),
                unit=None,
                source_record_id=(
                    f"{query.series_id}:{item['date']}:"
                    f"{item.get('realtime_start', query.release.vintage)}"
                ),
                canonical_url=url,
                artifact_sha256=digest,
                release=query.release,
                context=context,
            )
            for item in _parse_json(response.body).get("observations", [])
        ]
        return _fact_batch(facts, context)


class TreasuryFiscalDataAdapter:
    _BASE_URL = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"

    def __init__(self, *, transport: OfficialEvidenceTransport):
        self._transport = transport

    def fetch(
        self,
        query: TreasuryFiscalDataQuery,
        *,
        context: EvidenceFetchContext,
    ) -> OfficialEvidenceBatch:
        dataset_path = _safe_dataset_path(query.dataset_path)
        fields = (query.date_field, *query.context_fields, query.value_field)
        params = (
            ("filter", f"{query.date_field}:eq:{query.period}"),
            ("fields", ",".join(fields)),
        )
        url = f"{self._BASE_URL}/{dataset_path}?{urlencode(params, safe=',:')}"
        response = _get_json(self._transport, url)
        digest = sha256(response.body).hexdigest()
        short_dataset = dataset_path.rsplit("/", 1)[-1]
        facts = []
        for item in _parse_json(response.body).get("data", []):
            context_value = "|".join(str(item[field]) for field in query.context_fields) or "all"
            series_id = f"{short_dataset}.{query.value_field}"
            facts.append(
                _macro_fact(
                    source="treasury",
                    dataset=dataset_path,
                    series_id=series_id,
                    period=str(item[query.date_field]),
                    context_value=context_value,
                    value=str(item[query.value_field]),
                    unit=None,
                    source_record_id=f"{series_id}:{item[query.date_field]}:{context_value}",
                    canonical_url=url,
                    artifact_sha256=digest,
                    release=query.release,
                    context=context,
                )
            )
        return _fact_batch(facts, context)


class CensusDataAdapter:
    _BASE_URL = "https://api.census.gov/data"

    def __init__(self, *, transport: OfficialEvidenceTransport):
        self._transport = transport

    def fetch(
        self,
        query: CensusDataQuery,
        *,
        context: EvidenceFetchContext,
    ) -> OfficialEvidenceBatch:
        dataset_path = _safe_dataset_path(query.dataset_path)
        fields = (*query.context_fields, query.value_field)
        params = (("get", ",".join(fields)), (query.period_field, query.period))
        url = f"{self._BASE_URL}/{dataset_path}?{urlencode(params, safe=',')}"
        response = _get_json(self._transport, url)
        digest = sha256(response.body).hexdigest()
        rows = _parse_json(response.body)
        if not rows:
            return _fact_batch([], context)
        headers = tuple(rows[0])
        facts = []
        for values in rows[1:]:
            item = dict(zip(headers, values, strict=True))
            context_value = "|".join(str(item[field]) for field in query.context_fields) or "all"
            series_id = f"{dataset_path}.{query.value_field}"
            period = str(item[query.period_field])
            facts.append(
                _macro_fact(
                    source="census",
                    dataset=dataset_path,
                    series_id=series_id,
                    period=period,
                    context_value=context_value,
                    value=str(item[query.value_field]),
                    unit=None,
                    source_record_id=f"{series_id}:{period}:{context_value}",
                    canonical_url=url,
                    artifact_sha256=digest,
                    release=query.release,
                    context=context,
                )
            )
        return _fact_batch(facts, context)


def _normalize_cik(value: str) -> str:
    normalized = value.strip()
    if not normalized.isdigit() or len(normalized) > 10:
        raise ValueError("SEC CIK 必须为最多 10 位数字")
    return normalized.zfill(10)


def _require_response(response: TransportResponse, *, expected_url: str) -> None:
    if response.final_url != expected_url:
        raise EvidenceAdapterError(
            "source_redirect_rejected", "官方来源响应重定向被拒绝"
        )
    if response.status_code != 200:
        raise EvidenceAdapterError(
            "source_unavailable", f"官方来源暂不可用（HTTP {response.status_code}）"
        )


def _parse_json(body: bytes):
    try:
        return json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise EvidenceAdapterError(
            "source_payload_invalid", "官方来源返回无法解析的响应"
        ) from exc


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _parse_date_start(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def _get_json(
    transport: OfficialEvidenceTransport, url: str
) -> TransportResponse:
    response = transport.send(
        TransportRequest(
            method="GET",
            url=url,
            headers=(("Accept", "application/json"),),
        )
    )
    _require_response(response, expected_url=url)
    return response


def _fact_batch(
    facts: list[OfficialFact],
    context: EvidenceFetchContext,
    *,
    event_type: str = "macro_release",
) -> OfficialEvidenceBatch:
    events = tuple(
        OfficialEvent(
            identity=f"{fact.identity}:release-event",
            source=fact.source,
            event_type=event_type,
            evidence_identity=fact.identity,
            occurred_at=fact.published_at,
            available_from=fact.available_from,
            fetched_at=fact.fetched_at,
            authorization=fact.authorization,
            qualification=fact.qualification,
            health=fact.health,
        )
        for fact in facts
    )
    return OfficialEvidenceBatch(
        availability=Availability.AVAILABLE,
        health=context.health,
        facts=tuple(facts),
        events=events,
    )


def _safe_dataset_path(value: str) -> str:
    parts = value.split("/")
    if not parts or any(not part or not part.replace("_", "").replace("-", "").isalnum() for part in parts):
        raise ValueError("官方数据集路径无效")
    return "/".join(parts)


def _valid_allowed_ir_url(value: str) -> bool:
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and _decoded_ir_path(parsed.path) is not None
    )


def _url_matches_allowlist(actual_url: str, allowed_url: str) -> bool:
    actual = urlsplit(actual_url)
    allowed = urlsplit(allowed_url)
    try:
        authority_matches = (
            actual.scheme == "https"
            and actual.hostname == allowed.hostname
            and actual.port == allowed.port
            and actual.username is None
            and actual.password is None
        )
    except ValueError:
        return False
    actual_path = _decoded_ir_path(actual.path)
    allowed_path = _decoded_ir_path(allowed.path)
    if (
        not authority_matches
        or actual.query
        or actual.fragment
        or actual_path is None
        or allowed_path is None
    ):
        return False
    allowed_path = allowed_path.rstrip("/")
    return (
        not allowed_path
        or actual_path == allowed_path
        or actual_path.startswith(f"{allowed_path}/")
    )


def _decoded_ir_path(path: str) -> str | None:
    decoded = path
    while True:
        if "\\" in decoded or any(part in {".", ".."} for part in decoded.split("/")):
            return None
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            return decoded
        decoded = next_decoded


def _macro_fact(
    *,
    source: str,
    dataset: str,
    series_id: str,
    period: str,
    context_value: str,
    value: str,
    unit: str | None,
    source_record_id: str,
    canonical_url: str,
    artifact_sha256: str,
    release,
    context: EvidenceFetchContext,
) -> OfficialFact:
    logical_identity = f"{source}:{series_id}:{period}:{context_value}"
    return OfficialFact(
        identity=f"{logical_identity}:{release.vintage}:{release.revision}",
        logical_identity=logical_identity,
        source=source,
        dataset=dataset,
        series_id=series_id,
        reference_period=period,
        context=context_value,
        value=value,
        unit=unit,
        source_record_id=source_record_id,
        canonical_url=canonical_url,
        artifact_sha256=artifact_sha256,
        published_at=release.published_at,
        effective_at=release.effective_at,
        available_from=release.available_from,
        fetched_at=context.fetched_at,
        vintage=release.vintage,
        revision=release.revision,
        authorization=context.authorization,
        qualification=context.qualification,
        health=context.health,
        fallback_identity=context.fallback_identity,
    )
