from sentence_transformers import SentenceTransformer
import os

save_dir = "./models/bge-small-zh"
os.makedirs(save_dir, exist_ok=True)

print("开始下载 BAAI/bge-small-zh ...")
model = SentenceTransformer("BAAI/bge-small-zh")
model.save(save_dir)
print(f"模型保存完成，路径：{save_dir}")