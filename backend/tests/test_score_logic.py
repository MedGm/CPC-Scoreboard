import unittest
from backend import core

class TestScoreLogic(unittest.TestCase):

    def setUp(self):
        # Mock Data matching Codeforces structure
        self.problems = [
            {'index': 'A', 'name': 'Problem A'},
            {'index': 'B', 'name': 'Problem B'}
        ]
        
        # Rows need "party" to identify official contestants
        self.contestants_rows = [
            {
                'party': {'members': [{'handle': 'User1'}], 'participantType': 'CONTESTANT'},
                'rank': 1, 'maxPoints': 0, 'penalty': 0, 'problemResults': []
            },
            {
                'party': {'members': [{'handle': 'User2'}], 'participantType': 'CONTESTANT'},
                'rank': 2, 'maxPoints': 0, 'penalty': 0, 'problemResults': []
            }
        ]
        
        # Submissions need "author" and "problem"
        self.submissions = [
            {
                'id': 1,
                'author': {'members': [{'handle': 'User1'}], 'participantType': 'CONTESTANT'},
                'problem': {'index': 'A'},
                'verdict': 'OK', 'relativeTimeSeconds': 60
            },
            {
                'id': 2,
                'author': {'members': [{'handle': 'User2'}], 'participantType': 'CONTESTANT'},
                'problem': {'index': 'A'},
                'verdict': 'WRONG_ANSWER', 'relativeTimeSeconds': 120,
                'passedTestCount': 1  # Should count
            },
            {
                'id': 3,
                'author': {'members': [{'handle': 'User2'}], 'participantType': 'CONTESTANT'},
                'problem': {'index': 'A'},
                'verdict': 'OK', 'relativeTimeSeconds': 300,
                'passedTestCount': 10
            },
            {
                'id': 4,
                'author': {'members': [{'handle': 'User1'}], 'participantType': 'CONTESTANT'},
                'problem': {'index': 'B'},
                'verdict': 'OK', 'relativeTimeSeconds': 600,
                'passedTestCount': 5
            },
            {
                'id': 5,
                'author': {'members': [{'handle': 'User1'}], 'participantType': 'CONTESTANT'},
                'problem': {'index': 'B'},
                'verdict': 'WRONG_ANSWER', 'relativeTimeSeconds': 500, 
                'passedTestCount': 0 # Should NOT count
            }
        ]

    def test_standings_at_start(self):
        # Time 0
        standings = core.compute_standings_at_time(self.submissions, self.contestants_rows, self.problems, 0)
        u1 = next(c for c in standings if c['handle'] == 'User1')
        u2 = next(c for c in standings if c['handle'] == 'User2')
        self.assertEqual(u1['solved'], 0)
        self.assertEqual(u2['solved'], 0)

    def test_standings_after_first_solve(self):
        # Time 100 sec (User1 solved A at 60)
        standings = core.compute_standings_at_time(self.submissions, self.contestants_rows, self.problems, 100)
        u1 = next(c for c in standings if c['handle'] == 'User1')
        u2 = next(c for c in standings if c['handle'] == 'User2')
        
        # User1: 1 solve, penalty 1 min (60s)
        self.assertEqual(u1['solved'], 1)
        self.assertEqual(u1['penalty'], 1)
        
        # User2: 0 solves
        self.assertEqual(u2['solved'], 0)

    def test_standings_after_wrong_answer(self):
        # Time 200 sec (User2 WA at 120)
        standings = core.compute_standings_at_time(self.submissions, self.contestants_rows, self.problems, 200)
        u2 = next(c for c in standings if c['handle'] == 'User2')
        
        self.assertEqual(u2['solved'], 0)
        # Verify rejected attempts
        self.assertEqual(u2['problemResults']['A']['rejectedAttempts'], 1)

    def test_standings_after_second_solve_with_penalty(self):
        # Time 400 sec (User2 solved A at 300, after 1 WA)
        standings = core.compute_standings_at_time(self.submissions, self.contestants_rows, self.problems, 400)
        u2 = next(c for c in standings if c['handle'] == 'User2')
        
        self.assertEqual(u2['solved'], 1)
        # Penalty: 5 min (300s) + 20 min (1 WA) = 25
        self.assertEqual(u2['penalty'], 25)

    def test_full_standings(self):
        # Time 1000 sec (User1 solved B at 600)
        standings = core.compute_standings_at_time(self.submissions, self.contestants_rows, self.problems, 1000)
        u1 = next(c for c in standings if c['handle'] == 'User1')
        
        # User1: 2 solves. A(1 min) + B(10 min) = 11 penalty
        # The WA at 500s (id=5) had passedTestCount=0, so no penalty.
        self.assertEqual(u1['solved'], 2)
        self.assertEqual(u1['penalty'], 11)

    def test_ignore_test0_failure(self):
        # Verify that sub id 5 (User1, WB, passed=0) did not add rejected attempt
        # We need to check internal state or just penalty if we had solved it later
        standings = core.compute_standings_at_time(self.submissions, self.contestants_rows, self.problems, 1000)
        u1 = next(c for c in standings if c['handle'] == 'User1')
        # Check specific problem rejected count
        self.assertEqual(u1['problemResults']['B']['rejectedAttempts'], 0)

if __name__ == '__main__':
    unittest.main()
