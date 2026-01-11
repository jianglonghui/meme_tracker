import ast
import time
import argparse
import requests
import numpy as np
import pandas as pd
from tqdm import tqdm
from pprint import pprint
from concurrent.futures import ThreadPoolExecutor, as_completed
import dashscope
from http import HTTPStatus


SCORE_URL = "http://localhost:8000/score"
instruct = ""
with open("instruct.txt", "r", encoding="utf-8") as f:
    instruct = f.read()



def parse_to_list(s: str):
    if pd.isna(s):
        return []

    s = s.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]

    # 用中文逗号切分
    items = [x.strip() for x in s.split("，") if x.strip()]
    return items


def get_score(idx, query, doc):
    suffix = (
        "【以上推文包含代币名称，"
        "请问这个代币名称是？】\n\n"
    )
    payload = {
        "text_1": "【推文】：" + query + suffix,
        "text_2": "代币名称：" + doc,
    }

    try:
        response = requests.post(SCORE_URL, json=payload, timeout=10)
        response.raise_for_status()
        result = response.json()
        score = result.get("data", [])[0].get("score", 0.0)
    except Exception as e:
        print(f"[ERROR] doc={doc}, {e}")
        score = 0.0

    return idx, score


def get_score_aliyun(query, documents, top_n=10):
    full_query = "【推文】：\n" + query
    
    formatted_docs = ["代币名称：" + doc for doc in documents]
    
    try:
        resp = dashscope.TextReRank.call(
            model="qwen3-rerank",
            query=full_query,
            documents=formatted_docs,
            top_n=min(top_n, len(documents)),
            return_documents=False,
            instruct=instruct,
            temperature=0
        )
        
        if resp.status_code == HTTPStatus.OK:
            scores = np.zeros(len(documents), dtype=np.float32)
            results = resp.output.results
            
            for result in results:
                original_idx = result.index
                relevance_score = result.relevance_score
                scores[original_idx] = relevance_score
            
            return scores
        else:
            print(f"[ERROR] 阿里云API调用失败: {resp}")
            return np.zeros(len(documents), dtype=np.float32)
            
    except Exception as e:
        print(f"[ERROR] 阿里云API异常: {e}")
        return np.zeros(len(documents), dtype=np.float32)


def process_sample(args, query, documents, topk=1):
    if args.api_key:
        scores = get_score_aliyun(query, documents, top_n=topk)
    else:
        scores = np.zeros(len(documents), dtype=np.float32)

        with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
            futures = [
                executor.submit(get_score, idx, query, doc)
                for idx, doc in enumerate(documents)
            ]

            for future in as_completed(futures):
                idx, score = future.result()
                scores[idx] = score

    # print(query)
    # doc2score = [{doc: score} for doc, score in zip(documents, scores)]
    # pprint(doc2score)

    # 取 top-k 索引（按分数从高到低）
    topk_indices = np.argsort(-scores)[:topk].tolist()
    return topk_indices, scores


def run(args):
    data = pd.read_csv(args.input_file)

    queries = data["分析推文"].fillna("").astype(str).tolist()

    documents_list = [
        parse_to_list(x)
        for x in data["候选代币列表"]
    ]
    # labels_list = [[int(i)-1 for i in parse_to_list(x)] for x in data["正例索引"]]
    labels_list = [[1] for x in data["正例索引"]]

    precision = 0
    start_time = time.time()
    first_query_time = None
    
    label_docs_list = []
    topk_docs_list = []
    topk_scores_list = []

    for idx, (labels, query, documents) in enumerate(tqdm(zip(labels_list, queries, documents_list))):
        query_start = time.time()
        
        topk_indices, scores = process_sample(
            args, query, documents, topk=args.topk
        )
        
        query_end = time.time()
        
        # 记录首条查询耗时
        if idx == 0:
            first_query_time = query_end - query_start
        
        print(labels, topk_indices)
        if set(labels) & set(topk_indices):
            precision += 1
        
        # 获取标签对应的文档
        label_docs = [documents[idx] for idx in labels if idx < len(documents)]
        label_docs_list.append(label_docs)
        
        # 获取 topk 预测的文档
        topk_docs = [documents[idx] for idx in topk_indices if idx < len(documents)]
        topk_docs_list.append(topk_docs)
        
        # 获取 topk 对应的分数
        topk_scores = [float(scores[idx]) for idx in topk_indices if idx < len(documents)]
        topk_scores_list.append(topk_scores)

    end_time = time.time()

    print(f"样本数: {len(data)}")
    if first_query_time is not None:
        print(f"首条查询耗时: {first_query_time:.3f} 秒")
    print(f"平均单条耗时: {(end_time - start_time) / len(data):.3f} 秒")
    print(f"Precision@{args.topk}: {precision / len(data):.4f}")
    
    data["标签文档"] = label_docs_list
    data["预测文档"] = topk_docs_list
    data["预测分数"] = topk_scores_list
    
    output_file = args.input_file.replace(".csv", "_results.csv")
    data.to_csv(output_file, index=False)
    print(f"结果已保存到: {output_file}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_file", type=str, default="tweets_0110.csv")
    parser.add_argument("--max_workers", type=int, default=100)
    parser.add_argument("--topk", type=int, default=10)
    parser.add_argument("--api_key", type=str, default="sk-4eab2a9ab93742b0930fdf640808f254")
    args = parser.parse_args()

    if args.api_key:
        dashscope.api_key = args.api_key

    run(args)


if __name__ == "__main__":
    main()
