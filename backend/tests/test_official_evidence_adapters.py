from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import unittest

from backend.app.official_evidence.adapters import (
    BeaDataAdapter,
    BlsSeriesAdapter,
    CensusDataAdapter,
    FederalReserveSeriesAdapter,
    IssuerIrAdapter,
    SecCompanyFactsAdapter,
    SecSubmissionsAdapter,
    TreasuryFiscalDataAdapter,
)
from backend.app.official_evidence.contracts import (
    BeaDataQuery,
    BlsSeriesQuery,
    CensusDataQuery,
    EvidenceFetchContext,
    EvidenceQualification,
    FederalReserveSeriesQuery,
    IssuerIrDocumentQuery,
    ReleaseMetadata,
    SecAccessionAvailability,
    SecCompanyFactsQuery,
    SecSubmissionsQuery,
    SourceAuthorization,
    SourceHealth,
    TreasuryFiscalDataQuery,
    TransportResponse,
)
from backend.app.official_evidence.policy import (
    EvidenceIdentityConflict,
    ReleasePlan,
    ReleaseState,
    append_fact_versions,
    observe_release,
    read_fact_version,
    select_ai_evidence,
)


FIXTURES = Path(__file__).parent / "fixtures" / "official_evidence"
FETCHED_AT = datetime(2026, 8, 3, 1, 0, tzinfo=timezone.utc)


class ScriptedTransport:
    def __init__(self, body: bytes, *, final_url: str, status_code: int = 200):
        self.body = body
        self.final_url = final_url
        self.status_code = status_code
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        return TransportResponse(
            status_code=self.status_code,
            final_url=self.final_url,
            content_type="application/json",
            body=self.body,
        )


def traceable_context() -> EvidenceFetchContext:
    return EvidenceFetchContext(
        fetched_at=FETCHED_AT,
        health=SourceHealth.FRESH,
        qualification=EvidenceQualification.TRACEABLE_HISTORY,
        authorization=SourceAuthorization(
            snapshot_id="auth-sec-fixture-v1",
            source="official_primary",
            dataset="synthetic_official_fixture",
            plan="frozen_scripted_adapter",
            display=True,
            internal_analysis=True,
            ai_context=True,
            persist=True,
            backfill=True,
            redistribute=False,
            formal_research=False,
            terms_url="https://www.sec.gov/privacy.htm",
            checked_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            retention_policy="按来源条款保留",
            evidence_sha256="a" * 64,
        ),
    )


class OfficialEvidenceAdaptersTest(unittest.TestCase):
    def test_authorization_snapshot_requires_auditable_time_and_evidence_hash(self) -> None:
        authorization = traceable_context().authorization
        with self.assertRaisesRegex(ValueError, "checked_at_requires_timezone"):
            replace(authorization, checked_at=datetime(2026, 8, 1))
        with self.assertRaisesRegex(
            ValueError, "authorization_evidence_sha256_invalid"
        ):
            replace(authorization, evidence_sha256="not-a-digest")

    def test_issuer_ir_preserves_independent_release_metadata(self) -> None:
        body = (FIXTURES / "issuer_ir_malicious.html").read_bytes()
        url = "https://investor.synthetic.example/releases/q2-2026.html"
        published_at = datetime(2026, 7, 30, 20, 5, tzinfo=timezone.utc)
        available_from = datetime(2026, 7, 30, 20, 8, tzinfo=timezone.utc)
        vintage = "2026-07-30T20:08:30Z"
        revision = "same-day-correction-2"

        batch = IssuerIrAdapter(
            transport=ScriptedTransport(body, final_url=url),
            allowed_origins={
                "issuer-synth-1": "https://investor.synthetic.example/releases/"
            },
        ).fetch(
            IssuerIrDocumentQuery(
                issuer_id="issuer-synth-1",
                document_url=url,
                document_type="earnings_release",
                published_at=published_at,
                available_from=available_from,
                vintage=vintage,
                revision=revision,
            ),
            context=traceable_context(),
        )

        document = batch.documents[0]
        self.assertEqual(document.published_at, published_at)
        self.assertEqual(document.available_from, available_from)
        self.assertEqual(document.vintage, vintage)
        self.assertEqual(document.revision, revision)
        self.assertEqual(batch.events[0].occurred_at, published_at)
        self.assertEqual(batch.events[0].available_from, available_from)

    def test_sec_submissions_normalizes_accession_identity_and_provenance(self) -> None:
        body = (FIXTURES / "sec_submissions.json").read_bytes()
        url = "https://data.sec.gov/submissions/CIK0000000123.json"
        transport = ScriptedTransport(body, final_url=url)
        adapter = SecSubmissionsAdapter(
            transport=transport,
            user_agent="QuantitativeTrading fixture@example.invalid",
        )

        batch = adapter.fetch(
            SecSubmissionsQuery(cik="123"),
            context=traceable_context(),
        )

        self.assertEqual(len(transport.requests), 1)
        request = transport.requests[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.url, url)
        self.assertEqual(
            request.headers,
            (("Accept", "application/json"), ("User-Agent", "QuantitativeTrading fixture@example.invalid")),
        )
        self.assertEqual(batch.availability.value, "available")
        self.assertEqual(batch.health, SourceHealth.FRESH)
        self.assertEqual(len(batch.documents), 1)
        document = batch.documents[0]
        self.assertEqual(document.identity, "sec:accession:0000000123-26-000001")
        self.assertEqual(document.document_type, "10-Q")
        self.assertEqual(document.source_record_id, "0000000123-26-000001")
        self.assertEqual(document.artifact_sha256, sha256(body).hexdigest())
        self.assertEqual(document.published_at.isoformat(), "2026-07-30T20:15:30+00:00")
        self.assertEqual(document.effective_at.isoformat(), "2026-07-30T00:00:00+00:00")
        self.assertEqual(document.reference_period, "2026-06-30")
        self.assertEqual(document.available_from, document.published_at)
        self.assertEqual(document.fetched_at, FETCHED_AT)
        self.assertEqual(document.vintage, "2026-07-30T20:15:30+00:00")
        self.assertEqual(document.revision, "0000000123-26-000001")
        self.assertFalse(document.authorization.formal_research)
        self.assertTrue(document.authorization.ai_context)
        self.assertFalse(document.authorization.redistribute)
        self.assertEqual(document.authorization.source, "official_primary")
        self.assertEqual(len(document.authorization.evidence_sha256), 64)
        self.assertEqual(document.qualification, EvidenceQualification.TRACEABLE_HISTORY)
        self.assertEqual(document.health, SourceHealth.FRESH)
        self.assertEqual(batch.events[0].document_identity, document.identity)
        self.assertEqual(batch.events[0].event_type, "sec_filing")

    def test_sec_company_facts_keep_amended_accessions_as_distinct_versions(self) -> None:
        body = (FIXTURES / "sec_company_facts.json").read_bytes()
        url = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000000123.json"
        batch = SecCompanyFactsAdapter(
            transport=ScriptedTransport(body, final_url=url),
            user_agent="QuantitativeTrading fixture@example.invalid",
        ).fetch(
            SecCompanyFactsQuery(
                cik="123",
                taxonomy="us-gaap",
                tag="RevenueFromContractWithCustomerExcludingAssessedTax",
                unit="USD",
                accession_availability=(
                    SecAccessionAvailability(
                        accession="0000000123-26-000001",
                        available_from=datetime(
                            2026, 7, 30, 20, 15, 30, tzinfo=timezone.utc
                        ),
                    ),
                    SecAccessionAvailability(
                        accession="0000000123-26-000002",
                        available_from=datetime(
                            2026, 8, 1, 21, 0, tzinfo=timezone.utc
                        ),
                    ),
                ),
            ),
            context=traceable_context(),
        )

        self.assertEqual(len(batch.facts), 2)
        first, amended = batch.facts
        self.assertEqual(first.logical_identity, amended.logical_identity)
        self.assertNotEqual(first.identity, amended.identity)
        self.assertEqual(first.revision, "0000000123-26-000001")
        self.assertEqual(amended.revision, "0000000123-26-000002")
        self.assertEqual(first.value, "1000000")
        self.assertEqual(amended.value, "1010000")
        self.assertEqual(first.reference_period, "2026-06-30")
        self.assertEqual(first.context, "CY2026Q2")
        self.assertEqual(first.unit, "USD")
        self.assertEqual(first.artifact_sha256, sha256(body).hexdigest())
        self.assertEqual(
            first.available_from,
            datetime(2026, 7, 30, 20, 15, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(first.published_at, first.available_from)

    def test_bls_series_preserves_release_and_vintage_identity(self) -> None:
        body = (FIXTURES / "bls_series.json").read_bytes()
        url = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
        transport = ScriptedTransport(body, final_url=url)
        adapter = BlsSeriesAdapter(transport=transport)
        release = ReleaseMetadata(
            published_at=datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc),
            effective_at=datetime(2026, 6, 30, 23, 59, tzinfo=timezone.utc),
            available_from=datetime(2026, 7, 14, 12, 30, 5, tzinfo=timezone.utc),
            vintage="2026-07-14T12:30:00Z",
            revision="initial",
        )

        batch = adapter.fetch(
            BlsSeriesQuery(
                series_id="SYNTH-CPI-U",
                start_year=2026,
                end_year=2026,
                release=release,
            ),
            context=traceable_context(),
        )

        request = transport.requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.url, url)
        self.assertEqual(
            request.body,
            b'{"endyear":"2026","seriesid":["SYNTH-CPI-U"],"startyear":"2026"}',
        )
        fact = batch.facts[0]
        self.assertEqual(fact.logical_identity, "bls:SYNTH-CPI-U:2026-M06:all")
        self.assertEqual(
            fact.identity,
            "bls:SYNTH-CPI-U:2026-M06:all:2026-07-14T12:30:00Z:initial",
        )
        self.assertEqual(fact.value, "321.500")
        self.assertEqual(fact.reference_period, "2026-M06")
        self.assertEqual(fact.context, "all")
        self.assertEqual(fact.published_at, release.published_at)
        self.assertEqual(fact.effective_at, release.effective_at)
        self.assertEqual(fact.available_from, release.available_from)
        self.assertEqual(fact.fetched_at, FETCHED_AT)
        self.assertEqual(fact.vintage, release.vintage)
        self.assertEqual(fact.revision, release.revision)
        self.assertEqual(fact.artifact_sha256, sha256(body).hexdigest())

    def test_bea_fed_treasury_and_census_use_typed_source_normalizers(self) -> None:
        release = ReleaseMetadata(
            published_at=datetime(2026, 7, 30, 12, 30, tzinfo=timezone.utc),
            effective_at=datetime(2026, 6, 30, 23, 59, tzinfo=timezone.utc),
            available_from=datetime(2026, 7, 30, 12, 30, 3, tzinfo=timezone.utc),
            vintage="2026-07-30T12:30:00Z",
            revision="initial",
        )
        cases = (
            (
                "bea_data.json",
                "https://apps.bea.gov/api/data?datasetname=NIPA&method=GetData&TableName=T10101&Year=2026",
                BeaDataAdapter,
                BeaDataQuery(dataset_name="NIPA", table_name="T10101", year="2026", release=release),
                ("bea:T10101.1:2026Q2:all", "31,000.0", "Billions of dollars"),
            ),
            (
                "federal_reserve_observations.json",
                "https://api.stlouisfed.org/fred/series/observations?file_type=json&observation_end=2026-06-30&observation_start=2026-06-01&series_id=SYNTH-FED-RATE",
                FederalReserveSeriesAdapter,
                FederalReserveSeriesQuery(series_id="SYNTH-FED-RATE", start_date="2026-06-01", end_date="2026-06-30", release=release),
                ("federal_reserve:SYNTH-FED-RATE:2026-06-01:all", "4.125", None),
            ),
            (
                "treasury_data.json",
                "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/debt_to_penny?filter=record_date:eq:2026-07-31&fields=record_date,security_desc,debt_held_public_amt",
                TreasuryFiscalDataAdapter,
                TreasuryFiscalDataQuery(
                    dataset_path="v2/accounting/od/debt_to_penny",
                    date_field="record_date",
                    period="2026-07-31",
                    value_field="debt_held_public_amt",
                    context_fields=("security_desc",),
                    release=release,
                ),
                ("treasury:debt_to_penny.debt_held_public_amt:2026-07-31:Marketable", "29500000000000.00", None),
            ),
            (
                "census_data.json",
                "https://api.census.gov/data/2026/synthetic?get=NAME,SYNTH_VALUE&time=2026-Q2",
                CensusDataAdapter,
                CensusDataQuery(
                    dataset_path="2026/synthetic",
                    value_field="SYNTH_VALUE",
                    period_field="time",
                    period="2026-Q2",
                    context_fields=("NAME",),
                    release=release,
                ),
                ("census:2026/synthetic.SYNTH_VALUE:2026-Q2:Synthetic United States", "123.4", None),
            ),
        )

        for fixture, url, adapter_type, query, expected in cases:
            with self.subTest(source=adapter_type.__name__):
                body = (FIXTURES / fixture).read_bytes()
                transport = ScriptedTransport(body, final_url=url)
                fact = adapter_type(transport=transport).fetch(
                    query,
                    context=traceable_context(),
                ).facts[0]
                self.assertEqual(transport.requests[0].method, "GET")
                self.assertEqual(transport.requests[0].url, url)
                self.assertEqual(
                    (fact.logical_identity, fact.value, fact.unit),
                    expected,
                )
                self.assertEqual(fact.vintage, release.vintage)
                self.assertEqual(fact.revision, release.revision)
                self.assertEqual(fact.artifact_sha256, sha256(body).hexdigest())
                self.assertFalse(fact.authorization.formal_research)

    def test_macro_facts_expose_typed_release_events_for_timeline(self) -> None:
        body = (FIXTURES / "bls_series.json").read_bytes()
        url = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
        release = ReleaseMetadata(
            published_at=datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc),
            effective_at=datetime(2026, 6, 30, 23, 59, tzinfo=timezone.utc),
            available_from=datetime(2026, 7, 14, 12, 30, 5, tzinfo=timezone.utc),
            vintage="2026-07-14",
            revision="initial",
        )

        batch = BlsSeriesAdapter(
            transport=ScriptedTransport(body, final_url=url)
        ).fetch(
            BlsSeriesQuery("SYNTH-CPI-U", 2026, 2026, release),
            context=traceable_context(),
        )

        self.assertEqual(len(batch.events), 1)
        event = batch.events[0]
        self.assertEqual(event.event_type, "macro_release")
        self.assertEqual(event.evidence_identity, batch.facts[0].identity)
        self.assertEqual(event.occurred_at, release.published_at)
        self.assertEqual(event.available_from, release.available_from)

    def test_transport_failures_have_stable_codes_without_response_body(self) -> None:
        from backend.app.official_evidence.adapters import EvidenceAdapterError

        body = b"upstream secret diagnostic must not leak"
        url = "https://data.sec.gov/submissions/CIK0000000123.json"
        adapter = SecSubmissionsAdapter(
            transport=ScriptedTransport(body, final_url=url, status_code=503),
            user_agent="QuantitativeTrading fixture@example.invalid",
        )
        with self.assertRaises(EvidenceAdapterError) as unavailable:
            adapter.fetch(SecSubmissionsQuery("123"), context=traceable_context())
        self.assertEqual(unavailable.exception.code, "source_unavailable")
        self.assertNotIn("secret diagnostic", str(unavailable.exception))

        redirected = SecSubmissionsAdapter(
            transport=ScriptedTransport(body, final_url="https://attacker.invalid/capture"),
            user_agent="QuantitativeTrading fixture@example.invalid",
        )
        with self.assertRaises(EvidenceAdapterError) as redirect:
            redirected.fetch(SecSubmissionsQuery("123"), context=traceable_context())
        self.assertEqual(redirect.exception.code, "source_redirect_rejected")
        self.assertNotIn("attacker.invalid", str(redirect.exception))

        invalid_payload = SecSubmissionsAdapter(
            transport=ScriptedTransport(b"private upstream parse details {", final_url=url),
            user_agent="QuantitativeTrading fixture@example.invalid",
        )
        with self.assertRaises(EvidenceAdapterError) as invalid:
            invalid_payload.fetch(SecSubmissionsQuery("123"), context=traceable_context())
        self.assertEqual(invalid.exception.code, "source_payload_invalid")
        self.assertNotIn("private upstream", str(invalid.exception))

    def test_revision_chain_is_append_only_and_old_vintage_remains_readable(self) -> None:
        fixture = (FIXTURES / "bls_series.json").read_bytes()
        url = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
        first_release = ReleaseMetadata(
            published_at=datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc),
            effective_at=datetime(2026, 6, 30, 23, 59, tzinfo=timezone.utc),
            available_from=datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc),
            vintage="2026-07-14",
            revision="initial",
        )
        query = BlsSeriesQuery("SYNTH-CPI-U", 2026, 2026, first_release)
        first = BlsSeriesAdapter(
            transport=ScriptedTransport(fixture, final_url=url)
        ).fetch(query, context=traceable_context()).facts[0]
        revised = replace(
            first,
            identity=first.identity.replace(":2026-07-14:initial", ":2026-08-01:revised"),
            value="321.700",
            vintage="2026-08-01",
            revision="revised",
            artifact_sha256="b" * 64,
        )

        versions = append_fact_versions((), (first,))
        versions = append_fact_versions(versions, (revised,))
        versions = append_fact_versions(versions, (first,))

        self.assertEqual(len(versions), 2)
        self.assertEqual(read_fact_version(versions, first.identity).value, "321.500")
        self.assertEqual(read_fact_version(versions, revised.identity).value, "321.700")
        with self.assertRaises(EvidenceIdentityConflict):
            append_fact_versions(versions, (replace(first, value="forged"),))

    def test_ai_selection_is_fail_closed_and_contains_no_document_body(self) -> None:
        body = (FIXTURES / "issuer_ir_malicious.html").read_bytes()
        url = "https://investor.synthetic.example/releases/q2-2026.html"
        adapter = IssuerIrAdapter(
            transport=ScriptedTransport(body, final_url=url),
            allowed_origins={"issuer-synth-1": "https://investor.synthetic.example/releases/"},
        )
        query = IssuerIrDocumentQuery(
            issuer_id="issuer-synth-1",
            document_url=url,
            document_type="earnings_release",
            published_at=datetime(2026, 7, 30, 20, 5, tzinfo=timezone.utc),
            available_from=datetime(2026, 7, 30, 20, 5, tzinfo=timezone.utc),
            vintage="2026-07-30T20:05:00Z",
            revision="initial",
        )
        allowed = adapter.fetch(query, context=traceable_context())
        denied_context = replace(
            traceable_context(),
            authorization=replace(traceable_context().authorization, ai_context=False),
        )
        denied = adapter.fetch(query, context=denied_context)

        allowed_selection = select_ai_evidence(allowed)
        denied_selection = select_ai_evidence(denied)

        self.assertEqual(allowed_selection.document_ids, (allowed.documents[0].identity,))
        self.assertEqual(allowed_selection.fact_ids, ())
        self.assertEqual(allowed_selection.exclusions, ())
        self.assertNotIn("Ignore all previous instructions", repr(allowed_selection))
        self.assertEqual(denied_selection.document_ids, ())
        self.assertEqual(denied_selection.exclusions[0].reason, "ai_context_not_authorized")

    def test_release_calendar_states_do_not_masquerade_as_no_events(self) -> None:
        scheduled_at = datetime(2026, 8, 3, 12, 30, tzinfo=timezone.utc)
        plan = ReleasePlan(
            source="bls",
            release_id="synthetic-cpi-2026-07",
            scheduled_at=scheduled_at,
        )

        scheduled = observe_release(plan, observed_at=scheduled_at - timedelta(minutes=1))
        pending = observe_release(plan, observed_at=scheduled_at + timedelta(minutes=10))
        degraded = observe_release(plan, observed_at=scheduled_at + timedelta(minutes=30))
        unavailable = observe_release(plan, observed_at=scheduled_at + timedelta(minutes=61))
        available_empty = observe_release(
            plan,
            observed_at=scheduled_at + timedelta(minutes=6),
            fetch_completed=True,
            evidence_count=0,
        )

        self.assertEqual(scheduled.state, ReleaseState.SCHEDULED)
        self.assertEqual(pending.state, ReleaseState.PENDING)
        self.assertEqual(pending.reason, "release_pending")
        self.assertEqual(degraded.state, ReleaseState.DEGRADED)
        self.assertEqual(unavailable.state, ReleaseState.UNAVAILABLE)
        self.assertEqual(unavailable.reason, "source_unavailable")
        self.assertEqual(available_empty.state, ReleaseState.AVAILABLE)
        self.assertEqual(available_empty.evidence_count, 0)
        self.assertEqual(
            plan.planned_attempts,
            (
                scheduled_at + timedelta(minutes=5),
                scheduled_at + timedelta(minutes=15),
                scheduled_at + timedelta(minutes=60),
            ),
        )

    def test_issuer_ir_keeps_malicious_document_as_inert_evidence(self) -> None:
        body = (FIXTURES / "issuer_ir_malicious.html").read_bytes()
        url = "https://investor.synthetic.example/releases/q2-2026.html"
        transport = ScriptedTransport(body, final_url=url)
        adapter = IssuerIrAdapter(
            transport=transport,
            allowed_origins={"issuer-synth-1": "https://investor.synthetic.example/releases/"},
        )

        batch = adapter.fetch(
            IssuerIrDocumentQuery(
                issuer_id="issuer-synth-1",
                document_url=url,
                document_type="earnings_release",
                published_at=datetime(2026, 7, 30, 20, 5, tzinfo=timezone.utc),
                available_from=datetime(2026, 7, 30, 20, 5, tzinfo=timezone.utc),
                vintage="2026-07-30T20:05:00Z",
                effective_at=datetime(2026, 7, 30, 20, 0, tzinfo=timezone.utc),
                reference_period="2026-Q2",
                revision="initial",
            ),
            context=traceable_context(),
        )

        self.assertEqual(transport.requests[0].url, url)
        self.assertEqual(transport.requests[0].method, "GET")
        document = batch.documents[0]
        self.assertEqual(document.source, "issuer_ir")
        self.assertEqual(document.source_record_id, url)
        self.assertEqual(document.artifact_sha256, sha256(body).hexdigest())
        self.assertFalse(document.instructions_allowed)
        self.assertNotIn("Ignore all previous instructions", repr(batch))
        self.assertEqual(batch.facts, ())
        self.assertEqual(batch.events[0].event_type, "issuer_release")

        with self.assertRaisesRegex(ValueError, "allowlist"):
            adapter.fetch(
                IssuerIrDocumentQuery(
                    issuer_id="issuer-synth-1",
                    document_url="https://attacker.invalid/releases/q2.html",
                    document_type="earnings_release",
                    published_at=datetime(2026, 7, 30, 20, 5, tzinfo=timezone.utc),
                    available_from=datetime(2026, 7, 30, 20, 5, tzinfo=timezone.utc),
                    vintage="2026-07-30T20:05:00Z",
                    revision="initial",
                ),
                context=traceable_context(),
            )
        self.assertEqual(len(transport.requests), 1)

        bare_origin_adapter = IssuerIrAdapter(
            transport=transport,
            allowed_origins={"issuer-synth-1": "https://investor.synthetic.example"},
        )
        with self.assertRaisesRegex(ValueError, "allowlist"):
            bare_origin_adapter.fetch(
                replace(
                    IssuerIrDocumentQuery(
                        issuer_id="issuer-synth-1",
                        document_url=url,
                        document_type="earnings_release",
                        published_at=datetime(2026, 7, 30, 20, 5, tzinfo=timezone.utc),
                        available_from=datetime(2026, 7, 30, 20, 5, tzinfo=timezone.utc),
                        vintage="2026-07-30T20:05:00Z",
                        revision="initial",
                    ),
                    document_url=(
                        "https://investor.synthetic.example.attacker.invalid/release"
                    ),
                ),
                context=traceable_context(),
            )

    def test_issuer_ir_rejects_ambiguous_paths_before_transport(self) -> None:
        body = (FIXTURES / "issuer_ir_malicious.html").read_bytes()
        allowed = "https://investor.synthetic.example/releases/"
        rejected_urls = (
            "https://investor.synthetic.example/releases/../private/q2.html",
            "https://investor.synthetic.example/releases/%2e%2e/private/q2.html",
            "https://investor.synthetic.example/releases/%252e%252e/private/q2.html",
            "https://investor.synthetic.example/releases/q2.html?download=1",
            "https://investor.synthetic.example/releases/q2.html#latest",
        )

        for document_url in rejected_urls:
            with self.subTest(document_url=document_url):
                transport = ScriptedTransport(body, final_url=document_url)
                adapter = IssuerIrAdapter(
                    transport=transport,
                    allowed_origins={"issuer-synth-1": allowed},
                )

                with self.assertRaisesRegex(ValueError, "allowlist"):
                    adapter.fetch(
                        IssuerIrDocumentQuery(
                            issuer_id="issuer-synth-1",
                            document_url=document_url,
                            document_type="earnings_release",
                            published_at=datetime(
                                2026, 7, 30, 20, 5, tzinfo=timezone.utc
                            ),
                            available_from=datetime(
                                2026, 7, 30, 20, 8, tzinfo=timezone.utc
                            ),
                            vintage="2026-07-30T20:08:30Z",
                            revision="same-day-correction-2",
                        ),
                        context=traceable_context(),
                    )
                self.assertEqual(transport.requests, [])


if __name__ == "__main__":
    unittest.main()
