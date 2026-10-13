from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles

import os
# ======================
# AI助手角色设定
# ======================

SYSTEM_PROMPT = """

你是一个智能AI助手。

你同时具备音乐推荐能力。


规则：

如果用户询问音乐、歌曲、歌单，
或者表达自己的情绪：

例如：

我很难过
我压力很大
我睡不着
推荐几首歌
适合学习听什么歌


请进入音乐助手模式。


必须严格输出以下格式：


[MUSIC_CARD]

歌曲名: 歌曲名称

歌手: 歌手名称

理由: 推荐理由

[/MUSIC_CARD]



一次输出3首歌曲。


注意：

1. [MUSIC_CARD] 必须出现。
2. 不允许改成其他格式。
3. 不要输出：
歌曲名：
然后下一行写内容。

必须同一行。


正确：

歌曲名: 晚安

错误：

歌曲名:
晚安



如果用户不是音乐相关问题：

正常回答即可。


"""



import dashscope
from dashscope import Generation
from tavily import TavilyClient


from config import (
    DASHSCOPE_API_KEY,
    TAVILY_API_KEY
)


from database import (

    init_db,

    create_chat,

    get_chats,

    delete_chat,

    save_message,

    get_messages,

    update_chat_title

)





# ======================
# 千问API
# ======================


dashscope.api_key = DASHSCOPE_API_KEY





# ======================
# 创建APP
# ======================


app = FastAPI()
# Tavily联网搜索

tavily = TavilyClient(
    api_key=TAVILY_API_KEY
)






# ======================
# 跨域
# ======================


app.add_middleware(

    CORSMiddleware,

    allow_origins=["*"],

    allow_credentials=True,

    allow_methods=["*"],

    allow_headers=["*"],

)






# ======================
# 初始化数据库
# ======================


init_db()






# ======================
# 文件目录
# ======================


if not os.path.exists("uploads"):

    os.mkdir("uploads")



app.mount(

    "/uploads",

    StaticFiles(directory="uploads"),

    name="uploads"

)








# ======================
# 数据模型
# ======================


class Message(BaseModel):

    text:str

    chat_id:int







# ======================
# 首页
# ======================


@app.post("/chat")
def chat(message:Message):

    try:

        # ======================
        # 判断是否需要联网
        # ======================

        need_search_words = [

            "新闻",
            "今天",
            "最新",
            "最近",
            "现在",
            "价格",
            "天气",
            "股票",
            "比赛"

        ]


        need_search = any(

            word in message.text

            for word in need_search_words

        )



        # ======================
        # 联网搜索
        # ======================

        if need_search:


            search_result = tavily.search(

                query=message.text,

                max_results=3

            )


            web_content=""


            for item in search_result["results"]:

                web_content += item["content"] + "\n"



            prompt=f"""

{SYSTEM_PROMPT}


联网搜索内容：

{web_content}


用户问题：

{message.text}


请结合以上内容回答。

"""



        else:


            prompt=f"""

{SYSTEM_PROMPT}


用户问题：

{message.text}


请回答用户。

"""





        # ======================
        # 调用千问
        # ======================

        response = Generation.call(

            model="qwen-plus",

            prompt=prompt

        )



        ai_text=response.output.text



        ai_text=response.output.text






        # 自动修改聊天标题

        update_chat_title(

            message.chat_id,

            message.text[:15]

        )







        # 保存用户消息

        save_message(

            message.chat_id,

            "user",

            message.text

        )





        # 保存AI消息

        save_message(

            message.chat_id,

            "ai",

            ai_text

        )






        return {


            "reply":ai_text


        }






    except Exception as e:


        return {


            "reply":"AI错误："+str(e)


        }









# ======================
# 创建聊天
# ======================


@app.post("/create_chat")
def new_chat(title:str="新聊天"):


    chat_id=create_chat(title)



    return {


        "message":"创建成功",

        "id":chat_id


    }









# ======================
# 获取聊天列表
# ======================


@app.get("/chats")
def chats():


    return {


        "data":get_chats()


    }










# ======================
# 删除聊天
# ======================


@app.delete("/delete_chat/{id}")
def remove_chat(id:int):


    delete_chat(id)



    return {


        "message":"删除成功"


    }









# ======================
# 获取历史消息
# ======================


@app.get("/messages/{chat_id}")
def messages(chat_id:int):


    return {


        "data":get_messages(chat_id)


    }









# ======================
# 文件上传
# ======================


@app.post("/upload")
async def upload_file(file:UploadFile=File(...)):


    path="uploads/"+file.filename



    with open(path,"wb") as f:


        content=await file.read()


        f.write(content)




    return {


        "filename":file.filename,


        "message":"上传成功"


    }









# ======================
# 文件列表
# ======================


@app.get("/files")
def files():


    return {


        "files":os.listdir("uploads")


    }









# ======================
# 文件读取
# ======================


def read_file(path):


    text=""



    if path.endswith(".txt"):


        with open(

            path,

            "r",

            encoding="utf-8"

        ) as f:


            text=f.read()






    elif path.endswith(".pdf"):


        from pypdf import PdfReader


        reader=PdfReader(path)



        for page in reader.pages:


            result=page.extract_text()


            if result:


                text+=result








    elif path.endswith(".docx"):


        from docx import Document


        doc=Document(path)



        for p in doc.paragraphs:


            text+=p.text






    return text










# ======================
# 文件问答
# ======================


@app.post("/chat_file")
def chat_file(filename:str,question:str):


    try:



        content=read_file(

            "uploads/"+filename

        )




        prompt=f"""

你是一个文件分析助手。


文件内容：

{content}



用户问题：

{question}



请根据文件内容回答。

"""




        response=Generation.call(

            model="qwen-plus",

            prompt=prompt

        )




        return {


            "reply":response.output.text


        }






    except Exception as e:



        return {


            "reply":"文件分析失败："+str(e)


        }