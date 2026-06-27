from __future__ import annotations

import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from backend.app import main
from backend.app.database import Base
from backend.app.models import Asset, AssetDailyPrice, PortfolioSnapshot, WatchlistItem


class UsResearchDbTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def test_import_sample_persists_us_research_tables_without_duplicates(self):
        with self.Session() as db:
            first = main.import_us_research_sample_to_db(db=db)
            second = main.import_us_research_sample_to_db(db=db)

            self.assertEqual(first["status"], "ok")
            self.assertEqual(first["dbPersistence"], "sample_persisted")
            self.assertFalse(first["brokerConnected"])
            self.assertFalse(first["realHoldingsImported"])
            self.assertEqual(first["summary"]["assets"], 4)
            self.assertEqual(first["summary"]["assetDailyPrices"], 4)
            self.assertEqual(first["summary"]["watchlistItems"], 4)
            self.assertEqual(first["summary"]["portfolioSnapshots"], 1)
            self.assertEqual(second["summary"], first["summary"])

            self.assertEqual(db.scalar(select(func.count(Asset.id))), 4)
            self.assertEqual(db.scalar(select(func.count(AssetDailyPrice.id))), 4)
            self.assertEqual(db.scalar(select(func.count(WatchlistItem.id))), 4)
            self.assertEqual(db.scalar(select(func.count(PortfolioSnapshot.id))), 1)

            overview = main.build_us_research_db_overview(db)

        self.assertEqual(overview["source"], "db-sample")
        self.assertTrue(overview["isSample"])
        self.assertEqual(overview["dataBoundary"]["dbPersistence"], "sample_persisted")
        self.assertFalse(overview["dataBoundary"]["brokerConnected"])
        self.assertFalse(overview["dataBoundary"]["realHoldingsImported"])
        self.assertEqual(overview["counts"]["assets"], 4)
        self.assertEqual(overview["marketSnapshot"]["status"], "ok")
        self.assertEqual(overview["portfolioSnapshots"][0]["snapshotId"], "sample-latest")


if __name__ == "__main__":
    unittest.main()
