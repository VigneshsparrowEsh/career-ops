import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from jobharvest.career_ops import queue_job
from jobharvest.database import Database
from jobharvest.matching import score_job, location_fit, matches
from jobharvest.normalize import normalize, utcnow
from jobharvest.profile import get_profile, save_profile


PROFILE = {"name": "Test Candidate", "roles": ["Business Analyst", "Supply Chain Analyst", "Data Analyst", "Market Research Analyst", "Operations Analyst", "Project Manager"], "skills": ["SQL", "Python", "Power BI", "Excel"], "years_experience": 4, "country": "India", "city": "Bengaluru", "remote_only": False, "open_to_relocation": False, "authorized_countries": [], "preferred_locations": [], "salary_target": "40K–80K", "salary_currency": "USD", "resume": "Business analyst. Four years. SQL, Python, Power BI, Excel.", "ready": True}


def fixture(**changes):
    return {"role": "Senior Business Analyst", "description": "SQL Python Power BI Excel. 3 years experience.", "skills": [{"name": "SQL", "inferred": 0}], "location": "Bengaluru, India", "country": "India", "workplace_type": "onsite", "experience_min_years": 3, "last_seen_at": utcnow(), "active": 1, **changes}


class MatchingTests(unittest.TestCase):
    def test_home_based_remote_and_hybrid_classification(self):
        base={"external_id":"1","title":"Project Manager","company":"Test","url":"https://example.com/1","description":"Role details"}
        for location, expected in [("Home based - Worldwide","remote"),("Hybrid remote, India","hybrid"),("Office Based - Taipei, Taiwan","onsite")]:
            self.assertEqual(normalize(dict(base,location=location),"test")["workplace_type"],expected)

    def test_relevant_skill_experience_fit_and_no_false_skill_substrings(self):
        good = score_job(fixture(), PROFILE)
        poor = score_job(fixture(role="Senior Mechanical Engineer", description="We use MySQL", experience_min_years=12), PROFILE)
        self.assertGreater(good["score"], poor["score"])
        self.assertFalse(poor["role_relevant"])
        self.assertNotIn("SQL", poor["matched_skills"])
        self.assertTrue(any("12+" in gap for gap in poor["gaps"]))

    def test_all_six_role_families(self):
        for role in ["Business Analyst", "Supply Chain Planner", "Data Analyst", "Market Researcher", "Operations Analyst", "Project Manager"]:
            with self.subTest(role=role):
                self.assertTrue(score_job(fixture(role=role), PROFILE)["role_relevant"])

    def test_remote_country_restrictions_win_over_worldwide_marketing(self):
        for job in [fixture(location="Remote, United States",country="US",workplace_type="remote",description="Work from anywhere."), fixture(location="Worldwide",country=None,workplace_type="remote",description="You must be based in the United States.")]:
            self.assertEqual(location_fit(job, PROFILE)[0], "mismatch")
            self.assertLess(score_job(job,PROFILE)["score"],40)

    def test_worldwide_eligible_region_unknown_and_relocation(self):
        self.assertEqual(location_fit(fixture(location="Worldwide",country=None,workplace_type="remote"),PROFILE)[0],"compatible")
        self.assertEqual(location_fit(fixture(location="Remote",country=None,workplace_type="remote"),PROFILE)[0],"review")
        self.assertEqual(location_fit(fixture(location="Canada",country="CA"),PROFILE)[0],"mismatch")
        self.assertEqual(location_fit(fixture(location="Canada",country="CA"),dict(PROFILE,open_to_relocation=True))[0],"review")
        self.assertEqual(location_fit(fixture(location="Indiana, United States",country="US"),PROFILE)[0],"mismatch")

    def test_remote_only_and_staleness(self):
        job=fixture(last_seen_at="2026-01-01T00:00:00+00:00")
        match=score_job(job,dict(PROFILE,remote_only=True),now=datetime(2026,11,3,tzinfo=timezone.utc))
        self.assertEqual(match["eligibility"],"mismatch")
        self.assertTrue(match["stale"])
        self.assertTrue(any("Salary" in w for w in match["warnings"]))

    def test_technical_operations_is_adjacent_and_unknown_eligibility_is_capped(self):
        self.assertLess(score_job(fixture(role="Operations Engineer"),PROFILE)["score"],60)
        self.assertLess(score_job(fixture(location="Remote",country=None,workplace_type="remote"),PROFILE)["score"],80)
        for location in ["Remote US","Remote UK"]:
            self.assertEqual(location_fit(fixture(location=location,country=None,workplace_type="remote"),PROFILE)[0],"mismatch")
        self.assertEqual(location_fit(fixture(location="Worldwide",country=None,workplace_type="remote",description="US-only position."),PROFILE)[0],"mismatch")

    def test_ranking_filtering_saved_and_hidden_before_pagination(self):
        with tempfile.TemporaryDirectory() as temporary:
            db=Database(Path(temporary)/"jobs.sqlite3")
            for id,title,location in [("1","Mechanical Engineer","India"),("2","Data Analyst","India"),("3","Business Analyst","India"),("4","Data Analyst","United States")]:
                job=normalize({"external_id":id,"title":title,"company":"Company "+id,"description":"SQL, Python, Power BI, Excel", "location":location,"url":"https://example.com/"+id},"test")
                with db.connect() as c:
                    db.upsert(c,job,utcnow())
            ranked=matches(db,PROFILE,{},limit=1)
            self.assertEqual(ranked["total"],2)
            self.assertEqual(len(ranked["jobs"]),1)
            id=ranked["jobs"][0]["id"]
            db.set_preference(id,"saved","Review SQL requirement")
            self.assertEqual(db.search({"saved":"true"})["jobs"][0]["preference"]["notes"],"Review SQL requirement")
            db.set_preference(id,"hidden")
            self.assertEqual(matches(db,PROFILE,{})["total"],1)
            self.assertEqual(db.search({})["total"],4)
            self.assertEqual(matches(db,PROFILE,{"include_restricted":"true"})["total"],2)
            db.set_preference(id,"none")
            self.assertEqual(matches(db,PROFILE,{})["total"],2)


class ProfileIntegrationTests(unittest.TestCase):
    def test_canonical_profile_preserves_unrelated_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); (root/"config").mkdir()
            (root/"config/profile.yml").write_text(json.dumps({"candidate":{"email":"private@example.com"},"narrative":{"story":"Keep this"}}))
            result=save_profile(root,PROFILE)
            self.assertEqual(result["roles"],PROFILE["roles"])
            self.assertEqual(result["resume"].strip(),PROFILE["resume"])
            doc=json.loads((root/"config/profile.yml").read_text())
            self.assertEqual(doc["candidate"]["email"],"private@example.com")
            self.assertEqual(doc["narrative"]["story"],"Keep this")
            self.assertEqual(get_profile(root)["years_experience"],4)

    def test_invalid_profile_never_overwrites_cv(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); save_profile(root,PROFILE)
            original=(root/"cv.md").read_bytes()
            for patch in [{"years_experience":float("nan")},{"years_experience":True},{"roles":[]},{"country":""},{"skills":"SQL"},{"remote_only":"yes"}]:
                with self.assertRaises(ValueError):
                    save_profile(root,dict(PROFILE,**patch))
                self.assertEqual((root/"cv.md").read_bytes(),original)

    def test_queue_reuses_career_ops_inbox_and_does_not_invent_evaluation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            job=fixture(id=1,company="A | company",sources=[{"url":"https://example.com/job/1","active":1}])
            self.assertEqual(queue_job(root,job)["status"],"queued")
            self.assertEqual(queue_job(root,job)["status"],"existing")
            inbox=(root/"data/pipeline.md").read_text()
            self.assertEqual(inbox.count("https://example.com/job/1"),1)
            self.assertTrue((root/"jds/jobharvest-1.md").exists())
            self.assertFalse((root/"data/applications.md").exists())
