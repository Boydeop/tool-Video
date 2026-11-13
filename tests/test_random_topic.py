from collections import Counter
import random
import unittest
from studio.quiz import TOPICS, choose_random_topic

class RandomTopicTests(unittest.TestCase):
    def test_catalog_membership_and_exclusion(self):
        rng=random.Random(999)
        for previous in TOPICS:
            self.assertNotEqual(choose_random_topic({},previous,rng),previous)

    def test_underused_topics_have_more_weight(self):
        rng=random.Random(10)
        counts={topic:300 for topic in TOPICS}
        counts[TOPICS[0]]=0
        selected=[choose_random_topic(counts,rng=rng) for _ in range(50)]
        self.assertGreaterEqual(selected.count(TOPICS[0]),40)

    def test_different_rerolls(self):
        rng=random.Random(456)
        first=choose_random_topic({},rng=rng)
        second=choose_random_topic({},exclude=first,rng=rng)
        self.assertNotEqual(first,second)

if __name__ == "__main__": unittest.main()
