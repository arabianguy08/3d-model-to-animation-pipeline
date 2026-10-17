import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from companion_animation import jobs


class JobTests(unittest.TestCase):
    def test_background_job_survives_request_and_records_real_exit_and_log(self):
        with tempfile.TemporaryDirectory(prefix="companion worker ") as temporary:
            with patch.dict(os.environ, {"COMPANION_ANIMATION_HOME": temporary}):
                job = jobs.start([sys.executable, "-c", "print('actual process output')"], temporary)
                deadline = time.monotonic() + 20
                result = jobs.status(job["job_id"])
                while result["status"] == "running" and time.monotonic() < deadline:
                    time.sleep(.1)
                    result = jobs.status(job["job_id"])
                self.assertEqual(result["status"], "success", result)
                self.assertEqual(result["exit_code"], 0)
                self.assertIn("actual process output", result["tail"])
                self.assertTrue(Path(result["log"]).is_file())


if __name__ == "__main__":
    unittest.main()
