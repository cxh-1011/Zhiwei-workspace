from paper_agent import load_paper, ask_paper

print("开始加载论文...")

count = load_paper("uploads/paper.pdf")

print("论文知识库建立完成")
print("切片数量:", count)

print("====================")

answer = ask_paper("这篇论文主要讲了什么？创新点是什么？")

print(answer)
