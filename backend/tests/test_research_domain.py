from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import unittest

from sqlalchemy import Uuid, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app import main
from backend.app.database import Base
from backend.app.models import (
    FOLLOW_UP_PROPOSAL_STATUS_VALUES,
    FORMAL_RESEARCH_ORIGIN_VALUES,
    FORMAL_RESEARCH_PHASE_VALUES,
    PUBLICATION_STATUS_VALUES,
    RESEARCH_CONCLUSION_VALUES,
    RESEARCH_RUN_STATUS_VALUES,
    RESEARCH_PLAN_ACTION_VALUES,
    STRATEGY_LIFECYCLE_VALUES,
    FollowUpResearchProposal,
    FormalResearch,
    FrozenResearchPlan,
    ResearchEvaluation,
    ResearchEvaluationRun,
    ResearchEvidenceRef,
    ResearchEvent,
    ResearchPlanApproval,
    ResearchPublication,
    ResearchRun,
    StrategyDefinition,
)
from backend.app.research_catalog import (
    get_formal_research_detail,
    get_strategy_profile,
    list_strategy_profiles,
)


class ResearchDomainTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def seed_graph(self, db: Session) -> dict[str, object]:
        strategy = StrategyDefinition(
            strategy_id="sentinel_etf_baseline",
            display_name="ETF 哨兵基线",
            lifecycle_status="活跃",
            economic_thesis="只验证研究管线，不评价 alpha。",
            registry_version="1",
            code_commit="c" * 40,
            metadata_json={"scope": "etf_time_series", "nonFinite": float("nan")},
        )
        plan = FrozenResearchPlan(
            id="10000000-0000-0000-0000-000000000001",
            strategy_id=strategy.strategy_id,
            issue_number=100,
            version=1,
            schema_version="1",
            plan_sha256="a" * 64,
            code_commit="c" * 40,
            plan_json={"strategyId": strategy.strategy_id, "nonFinite": float("nan")},
        )
        approval = ResearchPlanApproval(
            id="10000000-0000-0000-0000-000000000002",
            plan_id=plan.id,
            action="approved",
            actor_login="Jettlin927",
            comment_id=10001,
            comment_body=f"批准研究 {plan.plan_sha256}",
            plan_sha256=plan.plan_sha256,
        )
        formal = FormalResearch(
            id="10000000-0000-0000-0000-000000000003",
            plan_id=plan.id,
            approval_id=approval.id,
            phase="evaluating",
        )
        run_values = {
            "formal_research_id": formal.id,
            "reproducibility_key": "b" * 64,
            "strategy_id": strategy.strategy_id,
            "status": "succeeded",
            "stage": "finalized",
            "config": {},
            "config_sha256": "d" * 64,
            "data_snapshot_id": None,
            "code_commit": "c" * 40,
            "environment_sha256": "e" * 64,
            "random_seed": 7,
            "result_fingerprint": "f" * 64,
            "artifact_root": "outputs/research-runs/runs",
        }
        runs = [
            ResearchRun(run_id="20000000-0000-0000-0000-000000000001", **run_values),
            ResearchRun(run_id="20000000-0000-0000-0000-000000000002", **run_values),
        ]
        event = ResearchEvent(
            id="30000000-0000-0000-0000-000000000001",
            formal_research_id=formal.id,
            run_id=runs[0].run_id,
            sequence_no=1,
            event_type="run_finalized",
            payload_json={"stage": "finalized", "nonFinite": float("nan")},
        )
        evaluation = ResearchEvaluation(
            id="40000000-0000-0000-0000-000000000001",
            formal_research_id=formal.id,
            version=1,
            conclusion="证据不足",
            evaluation_sha256="1" * 64,
            supporting_evidence=[{"statement": "两次运行可复现", "score": float("nan")}],
            opposing_evidence=[{"statement": "尚未评价 alpha"}],
            missing_evidence=[{"statement": "缺少匹配基准"}],
            limitations=[{"statement": "仅为哨兵"}],
            follow_up_recommendations=[{"statement": "保持为管线基线"}],
        )
        evaluation_runs = [
            ResearchEvaluationRun(evaluation_id=evaluation.id, run_id=run.run_id)
            for run in runs
        ]
        evidence = ResearchEvidenceRef(
            id="50000000-0000-0000-0000-000000000001",
            evaluation_id=evaluation.id,
            run_id=runs[0].run_id,
            kind="report",
            uri="artifacts://sentinel/report.json",
            sha256="2" * 64,
            metadata_json={"mediaType": "application/json", "nonFinite": float("inf")},
        )
        publication = ResearchPublication(
            id="60000000-0000-0000-0000-000000000001",
            formal_research_id=formal.id,
            evaluation_id=evaluation.id,
            version=1,
            status="published",
            publication_sha256="3" * 64,
            artifact_manifest_uri="artifacts://sentinel/manifest.json",
            issue_number=100,
            issue_comment_id=10002,
            published_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
        )
        proposal = FollowUpResearchProposal(
            id="70000000-0000-0000-0000-000000000001",
            strategy_id=strategy.strategy_id,
            source_evaluation_id=evaluation.id,
            source_evidence_ref_id=evidence.id,
            title="补充匹配基准评价",
            rationale="当前评价缺少匹配基准。",
            status="proposed",
            proposal_json={"inheritsApproval": False, "nonFinite": float("-inf")},
        )
        db.add_all(
            [
                strategy,
                plan,
                approval,
                formal,
                *runs,
                event,
                evaluation,
                *evaluation_runs,
                evidence,
                publication,
                proposal,
            ]
        )
        db.commit()
        return {
            "strategy": strategy,
            "plan": plan,
            "approval": approval,
            "formal": formal,
            "runs": runs,
            "event": event,
            "evaluation": evaluation,
            "evidence": evidence,
            "publication": publication,
            "proposal": proposal,
        }

    def test_status_namespaces_are_explicit_and_disjoint(self) -> None:
        self.assertEqual(
            RESEARCH_RUN_STATUS_VALUES,
            {"queued", "running", "retrying", "succeeded", "failed", "interrupted"},
        )
        self.assertEqual(STRATEGY_LIFECYCLE_VALUES, {"活跃", "暂停", "停止研究", "已归档"})
        self.assertEqual(RESEARCH_CONCLUSION_VALUES, {"研究通过", "有条件候选", "证据不足", "受阻", "不通过"})
        self.assertEqual(FORMAL_RESEARCH_PHASE_VALUES, {"approved", "active", "evaluating", "published", "stopped"})
        self.assertEqual(PUBLICATION_STATUS_VALUES, {"pending", "published", "failed"})
        self.assertEqual(FOLLOW_UP_PROPOSAL_STATUS_VALUES, {"proposed", "accepted", "rejected", "converted"})
        self.assertEqual(
            RESEARCH_PLAN_ACTION_VALUES,
            {"approved", "invalidated", "stopped", "historical_import"},
        )
        self.assertEqual(FORMAL_RESEARCH_ORIGIN_VALUES, {"native", "historical_import"})
        self.assertEqual(ResearchPlanApproval.__table__.c.action.type.length, 24)
        self.assertTrue(RESEARCH_RUN_STATUS_VALUES.isdisjoint(STRATEGY_LIFECYCLE_VALUES))
        self.assertTrue(RESEARCH_RUN_STATUS_VALUES.isdisjoint(RESEARCH_CONCLUSION_VALUES))

    def test_versioned_graph_keeps_runs_evaluation_and_publication_separate(self) -> None:
        with Session(self.engine) as db:
            graph = self.seed_graph(db)
            formal = graph["formal"]
            evaluation = graph["evaluation"]
            publication = graph["publication"]
            runs = graph["runs"]

            self.assertEqual(len(runs), 2)
            self.assertTrue(all(run.formal_research_id == formal.id for run in runs))
            self.assertEqual(evaluation.conclusion, "证据不足")
            self.assertEqual(publication.status, "published")
            self.assertNotEqual(runs[0].status, evaluation.conclusion)

            replacement = ResearchEvaluation(
                id="40000000-0000-0000-0000-000000000002",
                formal_research_id=formal.id,
                version=2,
                conclusion="不通过",
                evaluation_sha256="4" * 64,
                supersedes_evaluation_id=evaluation.id,
                supporting_evidence=[],
                opposing_evidence=[],
                missing_evidence=[],
                limitations=[],
                follow_up_recommendations=[],
            )
            db.add(replacement)
            db.commit()
            self.assertEqual(replacement.supersedes_evaluation_id, evaluation.id)

            listed = list_strategy_profiles(db)
            profile = get_strategy_profile(db, graph["strategy"].strategy_id)
            self.assertEqual(str(listed[0].latest_publication_evaluation_id), evaluation.id)
            self.assertEqual(listed[0].latest_publication_conclusion, "证据不足")
            self.assertEqual(
                str(profile.formal_researches[0].latest_publication_evaluation_id), evaluation.id
            )
            self.assertEqual(profile.formal_researches[0].latest_publication_conclusion, "证据不足")

    def test_frozen_and_evaluated_records_reject_orm_overwrite(self) -> None:
        with Session(self.engine) as db:
            graph = self.seed_graph(db)
            plan = graph["plan"]
            plan.plan_json = {"changed": True}
            with self.assertRaisesRegex(RuntimeError, "不可原地修改"):
                db.commit()

        with Session(self.engine) as db:
            evaluation = db.get(ResearchEvaluation, "40000000-0000-0000-0000-000000000001")
            self.assertIsNotNone(evaluation)
            db.delete(evaluation)
            with self.assertRaisesRegex(RuntimeError, "不可删除"):
                db.commit()

        with Session(self.engine) as db:
            publication = db.get(ResearchPublication, "60000000-0000-0000-0000-000000000001")
            self.assertIsNotNone(publication)
            publication.artifact_manifest_uri = "artifacts://changed/manifest.json"
            with self.assertRaisesRegex(RuntimeError, "已终态"):
                db.commit()

    def test_database_constraints_reject_cross_namespace_values(self) -> None:
        with Session(self.engine) as db:
            db.add(
                StrategyDefinition(
                    strategy_id="invalid",
                    display_name="错误状态",
                    lifecycle_status="succeeded",
                    economic_thesis="invalid",
                    registry_version="1",
                    code_commit="c" * 40,
                    metadata_json={},
                )
            )
            with self.assertRaises(IntegrityError):
                db.commit()

    def test_cross_layer_record_ids_use_uuid_columns(self) -> None:
        for model in (
            FrozenResearchPlan,
            ResearchPlanApproval,
            FormalResearch,
            ResearchEvent,
            ResearchEvaluation,
            ResearchEvidenceRef,
            ResearchPublication,
            FollowUpResearchProposal,
        ):
            with self.subTest(model=model.__name__):
                self.assertIsInstance(model.__table__.c.id.type, Uuid)
        self.assertIsInstance(ResearchRun.__table__.c.formal_research_id.type, Uuid)

    def test_read_only_catalog_projects_the_same_versioned_graph(self) -> None:
        with Session(self.engine) as db:
            graph = self.seed_graph(db)
            listed = list_strategy_profiles(db)
            profile = get_strategy_profile(db, graph["strategy"].strategy_id)
            research = get_formal_research_detail(db, graph["formal"].id)

        self.assertEqual([item.strategy_id for item in listed], ["sentinel_etf_baseline"])
        self.assertEqual(profile.formal_researches[0].latest_publication_conclusion, "证据不足")
        self.assertEqual(research.plan.plan_sha256, "a" * 64)
        self.assertEqual(research.origin, "native")
        self.assertEqual(research.approval.action, "approved")
        self.assertIsNone(research.approval.source_uri)
        self.assertEqual([run.run_id for run in research.runs], [
            "20000000-0000-0000-0000-000000000001",
            "20000000-0000-0000-0000-000000000002",
        ])
        self.assertEqual(research.evaluations[0].evidence_refs[0].sha256, "2" * 64)
        self.assertEqual(research.publications[0].publication_sha256, "3" * 64)
        self.assertFalse(research.follow_up_proposals[0].proposal_json["inheritsApproval"])
        self.assertIsNone(profile.metadata_json["nonFinite"])
        self.assertIsNone(research.plan.plan_json["nonFinite"])
        self.assertIsNone(research.events[0].payload_json["nonFinite"])
        self.assertIsNone(research.evaluations[0].supporting_evidence[0]["score"])
        self.assertIsNone(research.evaluations[0].evidence_refs[0].metadata_json["nonFinite"])
        self.assertIsNone(research.follow_up_proposals[0].proposal_json["nonFinite"])
        json.dumps(profile.model_dump(mode="json"), allow_nan=False)
        json.dumps(research.model_dump(mode="json"), allow_nan=False)

    def test_only_read_routes_are_exposed(self) -> None:
        route_methods = {
            route.path: set(route.methods or set())
            for route in main.app.routes
            if route.path.startswith("/api/research/")
        }
        self.assertIn("/api/research/strategies", route_methods)
        self.assertIn("/api/research/strategies/{strategy_id}", route_methods)
        self.assertIn("/api/research/formal-researches/{research_id}", route_methods)
        for path in (
            "/api/research/strategies",
            "/api/research/strategies/{strategy_id}",
            "/api/research/formal-researches/{research_id}",
        ):
            self.assertEqual(route_methods[path], {"GET"})

    def test_0007_migration_is_forward_only_and_links_legacy_runs(self) -> None:
        migration_path = (
            Path(__file__).resolve().parents[1]
            / "migrations"
            / "versions"
            / "0007_research_domain.py"
        )
        source = migration_path.read_text(encoding="utf-8")
        self.assertIn('down_revision = "0006_worker_heartbeats"', source)
        self.assertIn('op.batch_alter_table("research_runs")', source)
        self.assertIn("batch_op.add_column", source)
        self.assertIn("formal_research_id", source)
        self.assertIn("prevent_immutable_research_mutation", source)
        self.assertIn("ensure_research_relation_consistency", source)
        self.assertIn("prevent_published_evaluation_extension", source)
        self.assertIn("sa.Uuid(as_uuid=False)", source)
        self.assertIn("禁止自动降级", source)
        self.assertNotIn("op.drop_table", source)

    def test_0008_migration_keeps_history_import_distinct_from_approval(self) -> None:
        migration_path = (
            Path(__file__).resolve().parents[1]
            / "migrations"
            / "versions"
            / "0008_research_history_provenance.py"
        )
        source = migration_path.read_text(encoding="utf-8")
        self.assertIn('down_revision = "0007_research_domain"', source)
        self.assertIn("historical_import", source)
        self.assertIn("source_uri", source)
        self.assertIn("ensure_formal_research_origin_consistency", source)
        self.assertIn("ensure_historical_run_link_consistency", source)
        self.assertIn("ck_formal_researches_historical_phase", source)
        self.assertIn("NEW.origin = 'native' AND approval.action = 'approved'", source)
        self.assertIn("NEW.origin = 'historical_import'", source)
        self.assertIn("historical research cannot authorize new or mismatched run", source)
        self.assertIn("historical research run is immutable", source)
        self.assertIn("formal research identity is immutable", source)
        self.assertIn("禁止自动降级", source)


if __name__ == "__main__":
    unittest.main()
