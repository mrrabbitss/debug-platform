import unittest
from types import SimpleNamespace

from app.services.bundled_embedding_windows import create_windowed_embeddings, split_embedding_text
from app.services.retrieval_model_contracts import RetrievalModelError


class BundledEmbeddingWindowsTests(unittest.TestCase):
    def test_long_unicode_preserves_every_character(self):
        text = "日志检查：AP 离线。\n" * 200 + "尾部唯一根因：电源异常"
        pieces = split_embedding_text(text, lambda value: len(value) + 2)
        self.assertGreater(len(pieces), 1)
        self.assertEqual("".join(part for part, _ in pieces), text)
        self.assertTrue(all(count <= 512 for _, count in pieces))

    def test_boundary_includes_special_tokens(self):
        self.assertEqual(len(split_embedding_text("中" * 510, lambda value: len(value) + 2)), 1)
        self.assertEqual(len(split_embedding_text("中" * 511, lambda value: len(value) + 2)), 2)

    def test_short_and_long_inputs_keep_order_and_pool_all_windows(self):
        batches = []

        def tokenize(url, **kwargs):
            self.assertEqual(url, "http://127.0.0.1:9000/tokenize")
            self.assertTrue(kwargs["json"]["add_special"])
            return SimpleNamespace(raise_for_status=lambda: None,
                                   json=lambda: {"tokens": [1] * (len(kwargs["json"]["content"]) + 2)})

        def embed(**kwargs):
            values = kwargs["input"]
            self.assertLessEqual(sum(len(value) + 2 for value in values), 512)
            batches.extend(values)
            return SimpleNamespace(data=[SimpleNamespace(index=index, embedding=[float("尾" in value), 1.0])
                                         for index, value in reversed(list(enumerate(values)))],
                                   usage=SimpleNamespace(prompt_tokens=1, total_tokens=1))

        client = SimpleNamespace(base_url="http://127.0.0.1:9000/v1/", api_key="synthetic",
                                 embeddings=SimpleNamespace(create=embed))
        texts = ["短文本", "中" * 539 + "尾", "最后"]
        result = create_windowed_embeddings(client, SimpleNamespace(post=tokenize),
                                             {"model": "bge", "input": texts}, timeout=10)
        self.assertEqual("".join(batches), "".join(texts))
        self.assertEqual([item.index for item in result.data], [0, 1, 2])
        self.assertEqual(result.data[0].embedding, [0.0, 1.0])
        self.assertGreater(result.data[1].embedding[0], 0)
        for item in result.data:
            self.assertAlmostEqual(sum(value * value for value in item.embedding), 1)

    def test_impossible_single_character_fails_explicitly(self):
        with self.assertRaises(RetrievalModelError):
            split_embedding_text("中", lambda _: 513)


if __name__ == "__main__":
    unittest.main()
