import unittest

from executors.camoufox_zhihu_review import extract_review_packet


class ZhihuReviewExtractionTests(unittest.TestCase):
    def test_seven_participants_and_clean_timings(self):
        text = """
任务一：复古胶片单反相机
任务二：冬季小屋微缩景观
不废话，先展示 DeepSeek V4.1 Flash 用 Blender 做的作品。
先看看新手组：
MiniMax M3
Xiaomi MiMo V2.5
Doubao Seed 2.1 Pro
接下来是实力组：
GLM 5.3 Flash
Kimi K3
Qwen 3.8 Max
这次评委选择 GPT 5.6 Sol，并从四个维度打分，最终按 Borda 分数排名。
下面展示其它模型用相同提示词的生成结果，都是一次性输出。
第二个任务里 deepseek v4.1 flash 制作水准最高，造型相对传统，但细节很精致。
glm 5.3 flash 创意略优，但材质和光影细节不够，而且比较慢。
冬季小屋任务，deepseek 是 1212s (20分钟)，glm 是 3940s (65分钟)。
我觉得 deepseek v4.1 flash 最大的优势，是速度快、成本低的同时，审美在线。
实验环境
Blender 4.3
提示词
相同提示词。
""".strip()
        headings = [
            "任务一：复古胶片单反相机",
            "任务二：冬季小屋微缩景观",
            "先看看新手组：",
            "MiniMax M3",
            "Xiaomi MiMo V2.5",
            "Doubao Seed 2.1 Pro",
            "接下来是实力组：",
            "GLM 5.3 Flash",
            "Kimi K3",
            "Qwen 3.8 Max",
            "实验环境",
            "提示词",
        ]
        packet = extract_review_packet(text, headings, "7 家国产 AI 的 3D 建模横评，DeepSeek V4.1 Flash 太强了！")

        self.assertEqual(
            set(packet["participants"]),
            {"DeepSeek", "MiniMax", "Xiaomi", "Doubao", "GLM", "Kimi", "Qwen"},
        )
        timings = {(x["value"], x["unit"].lower()) for x in packet["timing_mentions"]}
        self.assertIn(("1212", "s"), timings)
        self.assertIn(("20", "分钟"), timings)
        self.assertIn(("3940", "s"), timings)
        self.assertIn(("65", "分钟"), timings)
        self.assertNotIn(("5.6", "s"), timings)
        self.assertGreaterEqual(len(packet["comparison_evidence"]), 3)
        self.assertIn("实验环境", packet["method_evidence"])
        self.assertIn("提示词", packet["method_evidence"])


if __name__ == "__main__":
    unittest.main()
