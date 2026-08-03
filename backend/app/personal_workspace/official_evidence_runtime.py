from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from backend.app.official_evidence.adapters import (
    BlsSeriesAdapter,
    EvidenceAdapterError,
    OfficialEvidenceTransport,
    SecCompanyFactsAdapter,
    SecSubmissionsAdapter,
)
from backend.app.official_evidence.contracts import (
    BlsSeriesQuery,
    EvidenceFetchContext,
    EvidenceQualification,
    ReleaseMetadata,
    SecAccessionAvailability,
    SecCompanyFactsQuery,
    SecSubmissionsQuery,
    SourceAuthorization,
    SourceHealth,
    TransportRequest,
    TransportResponse,
)

from .analysis import (
    AnalysisIntent,
    EvidenceCandidate,
    EvidenceReadResult,
)
from .contracts import PersonalActor


_AUTHORIZATION_FIELDS = frozenset(field.name for field in fields(SourceAuthorization))
_AUTHORIZATION_BOOLEAN_FIELDS = frozenset(
    {
        "display",
        "internal_analysis",
        "ai_context",
        "persist",
        "backfill",
        "redistribute",
        "formal_research",
    }
)
_SEC_QUERY_FIELDS = frozenset(
    {
        "query_id",
        "subject_id",
        "kind",
        "cik",
        "taxonomy",
        "tag",
        "unit",
        "authorization_snapshot_id",
    }
)
_BLS_QUERY_FIELDS = frozenset(
    {
        "query_id",
        "subject_id",
        "kind",
        "series_id",
        "start_year",
        "end_year",
        "release",
        "authorization_snapshot_id",
    }
)
_RELEASE_FIELDS = frozenset(
    {
        "published_at",
        "effective_at",
        "available_from",
        "vintage",
        "revision",
    }
)
_QUERY_CONFIG_FIELDS = frozenset(
    {
        "schema_version",
        "revision",
        "checked_at",
        "expires_at",
        "queries",
        "content_sha256",
    }
)
_AUTHORIZATION_CONFIG_FIELDS = frozenset(
    {
        "schema_version",
        "checked_at",
        "expires_at",
        "snapshots",
        "content_sha256",
    }
)


@dataclass(frozen=True)
class _SecCompanyFactsRuntimeQuery:
    query_id: str
    subject_id: str
    cik: str
    taxonomy: str
    tag: str
    unit: str
    authorization: SourceAuthorization


@dataclass(frozen=True)
class _BlsRuntimeQuery:
    query_id: str
    subject_id: str
    series_id: str
    start_year: int
    end_year: int
    release: ReleaseMetadata
    authorization: SourceAuthorization


class _ConfigError(ValueError):
    def __init__(self, gap: str) -> None:
        super().__init__(gap)
        self.gap = gap


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


class UrllibOfficialEvidenceTransport:
    def __init__(self, *, timeout_seconds: int = 20, opener: Any | None = None) -> None:
        if timeout_seconds <= 0:
            raise ValueError("official_evidence_timeout_invalid")
        self._timeout_seconds = timeout_seconds
        self._opener = opener or build_opener(_NoRedirectHandler())

    def send(self, request: TransportRequest) -> TransportResponse:
        outbound = Request(
            request.url,
            data=request.body,
            headers=dict(request.headers),
            method=request.method,
        )
        try:
            with self._opener.open(
                outbound, timeout=self._timeout_seconds
            ) as response:
                return TransportResponse(
                    status_code=response.status,
                    final_url=response.geturl(),
                    content_type=response.headers.get_content_type(),
                    body=response.read(),
                )
        except HTTPError as exc:
            return TransportResponse(
                status_code=exc.code,
                final_url=exc.geturl(),
                content_type=exc.headers.get("Content-Type", ""),
                body=exc.read(),
            )


class UnavailableOfficialAnalysisEvidenceReader:
    config_revision = "official-analysis-unavailable"

    def __init__(self, gap: str) -> None:
        self._gap = gap

    def __call__(
        self, actor: PersonalActor, intent: AnalysisIntent
    ) -> EvidenceReadResult:
        return EvidenceReadResult(candidates=(), gaps=(self._gap,))


class OfficialAnalysisEvidenceReader:
    def __init__(
        self,
        *,
        config_revision: str,
        queries: tuple[_SecCompanyFactsRuntimeQuery | _BlsRuntimeQuery, ...],
        sec_user_agent: str,
        transport: OfficialEvidenceTransport,
        clock: Callable[[], datetime],
    ) -> None:
        self.config_revision = config_revision
        self._queries = queries
        self._transport = transport
        self._clock = clock
        self._sec_user_agent = sec_user_agent

    def __call__(
        self, actor: PersonalActor, intent: AnalysisIntent
    ) -> EvidenceReadResult:
        del actor
        subject_ids = frozenset(subject.upper() for subject in intent.subject_ids)
        selected = tuple(
            query
            for query in self._queries
            if query.subject_id == "*" or query.subject_id in subject_ids
        )
        if not selected:
            return EvidenceReadResult(
                candidates=(), gaps=("official_evidence_query_not_configured",)
            )
        candidates: list[EvidenceCandidate] = []
        gaps: list[str] = []
        for query in selected:
            try:
                candidate = self._read_one(query)
            except (EvidenceAdapterError, KeyError, OSError, TypeError, ValueError):
                gaps.append(f"official_evidence_source_unavailable:{query.query_id}")
                continue
            if candidate is None:
                gaps.append(f"official_evidence_not_available:{query.query_id}")
            else:
                candidates.append(candidate)
        return EvidenceReadResult(
            candidates=tuple(candidates),
            gaps=tuple(dict.fromkeys(gaps)),
        )

    def _read_one(
        self, query: _SecCompanyFactsRuntimeQuery | _BlsRuntimeQuery
    ) -> EvidenceCandidate | None:
        now = self._clock()
        context = EvidenceFetchContext(
            fetched_at=now,
            health=SourceHealth.FRESH,
            qualification=EvidenceQualification.ONLINE_OBSERVATION,
            authorization=query.authorization,
        )
        if isinstance(query, _SecCompanyFactsRuntimeQuery):
            submissions = SecSubmissionsAdapter(
                transport=self._transport,
                user_agent=self._sec_user_agent,
            ).fetch(SecSubmissionsQuery(cik=query.cik), context=context)
            availability = tuple(
                SecAccessionAvailability(
                    accession=document.source_record_id,
                    available_from=document.available_from,
                )
                for document in submissions.documents
            )
            batch = SecCompanyFactsAdapter(
                transport=self._transport,
                user_agent=self._sec_user_agent,
            ).fetch(
                SecCompanyFactsQuery(
                    cik=query.cik,
                    taxonomy=query.taxonomy,
                    tag=query.tag,
                    unit=query.unit,
                    accession_availability=availability,
                    only_mapped_accessions=True,
                ),
                context=context,
            )
            field = "official_facts"
        else:
            batch = BlsSeriesAdapter(transport=self._transport).fetch(
                BlsSeriesQuery(
                    series_id=query.series_id,
                    start_year=query.start_year,
                    end_year=query.end_year,
                    release=query.release,
                ),
                context=context,
            )
            field = "macro_facts"
        qualified = tuple(
            fact
            for fact in batch.facts
            if fact.available_from <= now
            and fact.health is SourceHealth.FRESH
            and fact.authorization.internal_analysis
            and fact.authorization.ai_context
        )
        if not qualified:
            return None
        fact = max(qualified, key=lambda item: (item.available_from, item.identity))
        excerpt = (
            f"{fact.source} {fact.dataset} {fact.series_id} "
            f"reference_period={fact.reference_period} value={fact.value} "
            f"unit={fact.unit or 'not_available'} context={fact.context}"
        )
        return EvidenceCandidate(
            evidence_id=fact.identity,
            kind=("official_company_fact" if field == "official_facts" else "official_macro_fact"),
            source=fact.source,
            field=field,
            excerpt=excerpt,
            content_sha256=sha256(excerpt.encode("utf-8")).hexdigest(),
            authorized_for_ai=True,
            as_of=fact.available_from,
        )


def load_official_analysis_evidence_reader(
    *,
    query_file: str | Path,
    authorization_file: str | Path,
    sec_user_agent: str,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    transport: OfficialEvidenceTransport | None = None,
) -> OfficialAnalysisEvidenceReader | UnavailableOfficialAnalysisEvidenceReader:
    try:
        now = clock()
        query_payload = _load_hashed_config(query_file, now=now)
        authorization_payload = _load_hashed_config(authorization_file, now=now)
        if set(query_payload) != _QUERY_CONFIG_FIELDS or set(
            authorization_payload
        ) != _AUTHORIZATION_CONFIG_FIELDS:
            raise _ConfigError("official_evidence_config_unavailable")
        authorizations = _parse_authorizations(authorization_payload)
        queries = _parse_queries(query_payload, authorizations=authorizations)
        revision = _clean_text(query_payload, "revision")
        if not queries or not sec_user_agent.strip():
            raise _ConfigError("official_evidence_config_unavailable")
    except _ConfigError as exc:
        return UnavailableOfficialAnalysisEvidenceReader(exc.gap)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return UnavailableOfficialAnalysisEvidenceReader(
            "official_evidence_config_unavailable"
        )
    return OfficialAnalysisEvidenceReader(
        config_revision=revision,
        queries=queries,
        sec_user_agent=sec_user_agent.strip(),
        transport=transport or UrllibOfficialEvidenceTransport(),
        clock=clock,
    )


def _load_hashed_config(path: str | Path, *, now: datetime) -> Mapping[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise _ConfigError("official_evidence_config_unavailable")
    digest = payload.get("content_sha256")
    if not isinstance(digest, str):
        raise _ConfigError("official_evidence_config_hash_invalid")
    canonical_payload = dict(payload)
    canonical_payload.pop("content_sha256", None)
    canonical = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if sha256(canonical).hexdigest() != digest:
        raise _ConfigError("official_evidence_config_hash_invalid")
    if payload.get("schema_version") != 1:
        raise _ConfigError("official_evidence_config_unavailable")
    checked_at = _parse_time(_clean_text(payload, "checked_at"))
    expires_at = _parse_time(_clean_text(payload, "expires_at"))
    if checked_at > now or expires_at <= now:
        raise _ConfigError("official_evidence_config_stale")
    return payload


def _parse_authorizations(
    payload: Mapping[str, Any],
) -> dict[str, SourceAuthorization]:
    snapshots = payload.get("snapshots")
    if not isinstance(snapshots, list):
        raise _ConfigError("official_evidence_config_unavailable")
    parsed: dict[str, SourceAuthorization] = {}
    for raw in snapshots:
        if not isinstance(raw, Mapping) or set(raw) != _AUTHORIZATION_FIELDS:
            raise _ConfigError("official_evidence_config_unavailable")
        if any(type(raw[field]) is not bool for field in _AUTHORIZATION_BOOLEAN_FIELDS):
            raise _ConfigError("official_evidence_config_unavailable")
        snapshot = SourceAuthorization(
            **{
                **raw,
                "checked_at": _parse_time(_clean_text(raw, "checked_at")),
            }
        )
        if not (
            snapshot.display
            and snapshot.internal_analysis
            and snapshot.ai_context
            and snapshot.persist
            and not snapshot.redistribute
            and not snapshot.formal_research
        ):
            raise _ConfigError("official_evidence_config_unavailable")
        if snapshot.snapshot_id in parsed:
            raise _ConfigError("official_evidence_config_unavailable")
        parsed[snapshot.snapshot_id] = snapshot
    return parsed


def _parse_queries(
    payload: Mapping[str, Any],
    *,
    authorizations: Mapping[str, SourceAuthorization],
) -> tuple[_SecCompanyFactsRuntimeQuery | _BlsRuntimeQuery, ...]:
    raw_queries = payload.get("queries")
    if not isinstance(raw_queries, list):
        raise _ConfigError("official_evidence_config_unavailable")
    parsed = []
    query_ids: set[str] = set()
    for raw in raw_queries:
        if not isinstance(raw, Mapping):
            raise _ConfigError("official_evidence_config_unavailable")
        query_id = _clean_text(raw, "query_id")
        if query_id in query_ids:
            raise _ConfigError("official_evidence_config_unavailable")
        query_ids.add(query_id)
        subject_id = _clean_text(raw, "subject_id").upper()
        authorization = authorizations[_clean_text(raw, "authorization_snapshot_id")]
        kind = raw.get("kind")
        if kind == "sec_companyfacts":
            if set(raw) != _SEC_QUERY_FIELDS:
                raise _ConfigError("official_evidence_config_unavailable")
            if authorization.source != "sec" or not authorization.dataset.startswith(
                "companyfacts:"
            ):
                raise _ConfigError("official_evidence_config_unavailable")
            parsed.append(
                _SecCompanyFactsRuntimeQuery(
                    query_id=query_id,
                    subject_id=subject_id,
                    cik=_clean_text(raw, "cik"),
                    taxonomy=_clean_text(raw, "taxonomy"),
                    tag=_clean_text(raw, "tag"),
                    unit=_clean_text(raw, "unit"),
                    authorization=authorization,
                )
            )
        elif kind == "bls_series":
            if set(raw) != _BLS_QUERY_FIELDS:
                raise _ConfigError("official_evidence_config_unavailable")
            if (
                authorization.source != "bls"
                or authorization.dataset != "public_api_v2_timeseries"
            ):
                raise _ConfigError("official_evidence_config_unavailable")
            release = raw.get("release")
            if not isinstance(release, Mapping) or set(release) != _RELEASE_FIELDS:
                raise _ConfigError("official_evidence_config_unavailable")
            start_year = _integer(raw, "start_year")
            end_year = _integer(raw, "end_year")
            if start_year > end_year or end_year - start_year > 20:
                raise _ConfigError("official_evidence_config_unavailable")
            parsed.append(
                _BlsRuntimeQuery(
                    query_id=query_id,
                    subject_id=subject_id,
                    series_id=_clean_text(raw, "series_id"),
                    start_year=start_year,
                    end_year=end_year,
                    release=ReleaseMetadata(
                        published_at=_parse_time(_clean_text(release, "published_at")),
                        effective_at=_optional_time(release.get("effective_at")),
                        available_from=_parse_time(
                            _clean_text(release, "available_from")
                        ),
                        vintage=_clean_text(release, "vintage"),
                        revision=_clean_text(release, "revision"),
                    ),
                    authorization=authorization,
                )
            )
        else:
            raise _ConfigError("official_evidence_config_unavailable")
    return tuple(parsed)


def _clean_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload[key]
    if not isinstance(value, str) or not value or value != value.strip():
        raise _ConfigError("official_evidence_config_unavailable")
    return value


def _integer(payload: Mapping[str, Any], key: str) -> int:
    value = payload[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise _ConfigError("official_evidence_config_unavailable")
    return value


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise _ConfigError("official_evidence_config_unavailable")
    return parsed.astimezone(timezone.utc)


def _optional_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _ConfigError("official_evidence_config_unavailable")
    return _parse_time(value)
