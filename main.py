import asyncio
import json

import openai
import plugins
import os

from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from channel.chat_message import ChatMessage
from channel.wechat.wechat_channel import WechatChannel
from channel.wechatcom.wechatcomapp_channel import WechatComAppChannel
from channel.wechatmp.wechatmp_channel import WechatMPChannel
from config import conf
from plugins import *
from common.log import logger
from plugins.newgpt_turbo.lib import function as fun, get_stock_info as stock, search_google as google
from datetime import datetime
from bridge.bridge import Bridge


def create_channel_object():
    channel_type = conf().get("channel_type")
    if channel_type in ['wechat', 'wx', 'wxy']:
        return WechatChannel()
    elif channel_type == 'wechatmp':
        return WechatMPChannel()
    elif channel_type == 'wechatmp_service':
        return WechatMPChannel()
    elif channel_type == 'wechatcom_app':
        return WechatComAppChannel()
    else:
        return WechatChannel()


@plugins.register(name="NewGpt_Turbo", desc="GPT函数调用，极速联网", desire_priority=-888, version="0.1", author="chazzjimel", )
class NewGpt(Plugin):
    def __init__(self):
        super().__init__()
        curdir = os.path.dirname(__file__)
        config_path = os.path.join(curdir, "config.json")
        logger.info(f"[newgpt_turbo] current directory: {curdir}")
        logger.info(f"加载配置文件: {config_path}")
        if not os.path.exists(config_path):
            logger.info('[RP] 配置文件不存在，将使用config.json.template模板')
            config_path = os.path.join(curdir, "config.json.template")
            logger.info(f"[newgpt_turbo] config template path: {config_path}")
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
                logger.debug(f"[newgpt_turbo] config content: {config}")
                openai.api_key = conf().get("open_ai_api_key")
                openai.api_base = conf().get("open_ai_api_base")
                self.alapi_key = config["alapi_key"]
                self.bing_subscription_key = config["bing_subscription_key"]
                self.google_api_key = config["google_api_key"]
                self.google_cx_id = config["google_cx_id"]
                self.functions_openai_model = config["functions_openai_model"]
                self.assistant_openai_model = config["assistant_openai_model"]
                self.app_key = config["app_key"]
                self.app_sign = config["app_sign"]
                self.temperature = config.get("temperature", 0.9)
                self.max_tokens = config.get("max_tokens", 1000)
                self.google_base_url = config.get("google_base_url", "https://www.googleapis.com/customsearch/v1?")
                self.comapp = create_channel_object()
                self.prompt = config["prompt"]
                self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
                logger.info("[newgpt_turbo] inited")
        except Exception as e:
            if isinstance(e, FileNotFoundError):
                logger.warn(f"[RP] init failed, config.json not found.")
            else:
                logger.warn("[RP] init failed." + str(e))
            raise e

    def on_handle_context(self, e_context: EventContext):
        if e_context["context"].type not in [ContextType.TEXT]:
            return

        reply = Reply()  # 创建一个回复对象
        reply.type = ReplyType.TEXT
        context = e_context['context'].content[:]
        logger.debug("context:%s" % context)
        all_sessions = Bridge().get_bot("chat").sessions
        session = all_sessions.session_query(context, e_context["context"]["session_id"], add_to_history=False)
        logger.debug("session.messages:%s" % session.messages)
        if len(session.messages) > 2:
            input_messages = session.messages[-2:]
        else:
            input_messages = session.messages[-1:]
        input_messages.append({"role": "user", "content": context})
        logger.debug("input_messages:%s" % input_messages)
        conversation_output = self.run_conversation(input_messages, e_context)
        if conversation_output is not None:
            _reply = conversation_output
            logger.debug("conversation_output:%s" % conversation_output)
            all_sessions.session_query(context, e_context["context"]["session_id"])
            all_sessions.session_reply(_reply, e_context["context"]["session_id"])
            reply.content = _reply
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            return
        else:
            return

    def run_conversation(self, input_messages, e_context: EventContext):
        global function_response
        content = e_context['context'].content[:]
        messages = []
        logger.debug(f"User input: {input_messages}")  # 用户输入
        response = openai.ChatCompletion.create(
            model=self.functions_openai_model,
            messages=input_messages,
            functions=[
                {
                    "name": "get_weather",
                    "description": "获取全球指定城市的天气信息",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "cityNm": {
                                "type": "string",
                                "description": "City names using Chinese characters, such as: 广州, 深圳, 东京, 伦敦",
                            },

                        },
                        "required": ["cityNm"],
                    },
                },
                {
                    "name": "get_morning_news",
                    "description": "获取每日早报信息",
                    "parameters": {
                        "type": "object",
                        "properties": {}
                    }
                },
                {
                    "name": "get_hotlist",
                    "description": "获取各种平台热榜信息",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "description": "type类型: '知乎':zhihu', '微博':weibo', '微信':weixin', '百度':baidu', '头条':toutiao', '163':163', 'xl', '36氪':36k', 'hitory', 'sspai', 'csdn', 'juejin', 'bilibili', 'douyin', '52pojie', 'v2ex', 'hostloc'",
                            }
                        },
                        "required": ["type"],
                    }
                },
                {
                    "name": "search",
                    "description": "默认搜索工具，谷歌和必应的搜索引擎",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "提供需要搜索的关键词信息即可",
                            },
                            "count": {
                                "type": "string",
                                "description": "搜索页数,如无指定几页，默认2，最大值10",
                            }

                        },
                        "required": ["query", "count"],
                    },
                },
                {
                    "name": "get_oil_price",
                    "description": "获取中国全国油价信息",
                    "parameters": {
                        "type": "object",
                        "properties": {}
                    }
                },
                {
                    "name": "get_Constellation_analysis",
                    "description": "获取十二星座运势",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "star": {
                                "type": "string",
                                "description": """       
                                        "白羊座": "aries",
                                        "金牛座": "taurus",
                                        "双子座": "gemini",
                                        "巨蟹座": "cancer",
                                        "狮子座": "leo",
                                        "处女座": "virgo",
                                        "天秤座": "libra",
                                        "天蝎座": "scorpio",
                                        "射手座": "sagittarius",
                                        "摩羯座": "capricorn",
                                        "水瓶座": "aquarius",
                                        "双鱼座": "pisces"""
                            },

                        },
                        "required": ["star"],
                    },
                },
                {
                    "name": "music_search",
                    "description": "音乐搜索，获得音乐信息",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "keyword": {
                                "type": "string",
                                "description": "需要搜索的音乐关键词信息",
                            },

                        },
                        "required": ["keyword"],
                    },
                },
                {
                    "name": "get_datetime",
                    "description": "获取全球指定城市实时日期时间和星期信息",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city_en": {
                                "type": "string",
                                "description": "需要查询的城市小写英文名，英文名中间空格用-代替，如beijing，new-york",
                            },

                        },
                        "required": ["city_en"],
                    },
                },
                {
                    "name": "get_url",
                    "description": "访问并获取URL的内容",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {
                                "type": "string",
                                "description": "需要访问的指定URL",
                            },

                        },
                        "required": ["url"],
                    },
                },
                {
                    "name": "get_stock_info",
                    "description": "获取上市股票实时信息的函数",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "stock_names": {
                                "type": "string",
                                "description": "股票中文名字简写，如果有多个，请空格隔开，不能有多余字符，如平安银行则传递平安、中新股份则传递中新",
                            },

                        },
                        "required": ["stock_names"],
                    },
                },
                {
                    "name": "search_bing_news",
                    "description": "实时新闻搜索引擎",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "提供需要搜索的新闻关键词信息",
                            },
                            "count": {
                                "type": "string",
                                "description": "搜索页数,如无指定几页，默认10，最大值50",
                            }

                        },
                        "required": ["query", "count"],
                    },
                },
                {
                    "name": "get_video_url",
                    "description": "通过原始URL解析可下载视频的URL函数",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {
                                "type": "string",
                                "description": "提供需要解析的URL",
                            },
                        },
                        "required": ["url"],
                    },
                },
            ],
            function_call="auto",
        )

        message = response["choices"][0]["message"]

        # 检查模型是否希望调用函数
        if message.get("function_call"):
            function_name = message["function_call"]["name"]
            logger.debug(f"Function call: {function_name}")  # 打印函数调用
            logger.debug(f"message={message}")
            # 处理各种可能的函数调用，执行函数并获取函数的返回结果
            if function_name == "get_weather":
                function_args = json.loads(message["function_call"].get("arguments", "{}"))
                logger.debug(f"Function arguments: {function_args}")  # 打印函数参数
                function_response = fun.get_weather(appkey=self.app_key, sign=self.app_sign,
                                                    cityNm=function_args.get("cityNm", "未指定地点"))
                function_response = json.dumps(function_response, ensure_ascii=False)
                logger.debug(f"Function response: {function_response}")  # 打印函数响应
            elif function_name == "get_morning_news":
                function_response = fun.get_morning_news(api_key=self.alapi_key)
                logger.debug(f"Function response: {function_response}")  # 打印函数响应
            elif function_name == "get_hotlist":
                function_args_str = message["function_call"].get("arguments", "{}")
                function_args = json.loads(function_args_str)  # 使用 json.loads 将字符串转换为字典
                hotlist_type = function_args.get("type", "未指定类型")
                function_response = fun.get_hotlist(api_key=self.alapi_key, type=hotlist_type)
                function_response = json.dumps(function_response, ensure_ascii=False)
                logger.debug(f"Function response: {function_response}")  # 打印函数响应
            elif function_name == "search":
                function_args_str = message["function_call"].get("arguments", "{}")
                function_args = json.loads(function_args_str)  # 使用 json.loads 将字符串转换为字典
                search_query = function_args.get("query", "未指定关键词")
                search_count = function_args.get("count", 1)
                if "必应" in content or "newbing" in content.lower():
                    com_reply = Reply()
                    com_reply.type = ReplyType.TEXT
                    context = e_context['context']
                    if context.kwargs.get('isgroup'):
                        msg = context.kwargs.get('msg')  # 这是WechatMessage实例
                        nickname = msg.actual_user_nickname  # 获取nickname
                        com_reply.content = "@{name}\n☑️正在给您实时联网必应搜索\n⏳整理深度数据需要时间，请耐心等待...".format(
                            name=nickname)
                    else:
                        com_reply.content = "☑️正在给您实时联网必应搜索\n⏳整理深度数据需要时间，请耐心等待..."
                    if self.comapp is not None:
                        self.comapp.send(com_reply, e_context['context'])
                    function_response = fun.search_bing(subscription_key=self.bing_subscription_key, query=search_query,
                                                        count=int(search_count))
                    function_response = json.dumps(function_response, ensure_ascii=False)
                    logger.debug(f"Function response: {function_response}")  # 打印函数响应
                elif "谷歌" in content or "搜索" in content or "google" in content.lower():
                    com_reply = Reply()
                    com_reply.type = ReplyType.TEXT
                    context = e_context['context']
                    if context.kwargs.get('isgroup'):
                        msg = context.kwargs.get('msg')  # 这是WechatMessage实例
                        nickname = msg.actual_user_nickname  # 获取nickname
                        com_reply.content = "@{name}\n☑️正在给您实时联网谷歌搜索\n⏳整理深度数据需要几分钟，请您耐心等待...".format(
                            name=nickname)
                    else:
                        com_reply.content = "☑️正在给您实时联网谷歌搜索\n⏳整理深度数据需要几分钟，请您耐心等待..."
                    if self.comapp is not None:
                        self.comapp.send(com_reply, e_context['context'])
                    function_response = google.search_google(search_terms=search_query, base_url=self.google_base_url,iterations=1, count=1,
                                                             api_key=self.google_api_key, cx_id=self.google_cx_id,
                                                             model=self.assistant_openai_model)
                    logger.debug(f"google.search_google url: {self.google_base_url}")
                    function_response = json.dumps(function_response, ensure_ascii=False)
                    logger.debug(f"Function response: {function_response}")  # 打印函数响应
                else:
                    return None
            elif function_name == "get_oil_price":
                function_response = fun.get_oil_price(api_key=self.alapi_key)
                logger.debug(f"Function response: {function_response}")  # 打印函数响应
            elif function_name == "get_Constellation_analysis":
                function_args = json.loads(message["function_call"].get("arguments", "{}"))
                logger.debug(f"Function arguments: {function_args}")  # 打印函数参数

                function_response = fun.get_Constellation_analysis(api_key=self.alapi_key,
                                                                   star=function_args.get("star", "未指定星座"),
                                                                   )
                function_response = json.dumps(function_response, ensure_ascii=False)
                logger.debug(f"Function response: {function_response}")  # 打印函数响应
            elif function_name == "music_search":
                function_args = json.loads(message["function_call"].get("arguments", "{}"))
                logger.debug(f"Function arguments: {function_args}")  # 打印函数参数

                function_response = fun.music_search(api_key=self.alapi_key,
                                                     keyword=function_args.get("keyword", "未指定音乐"),
                                                     )
                function_response = json.dumps(function_response, ensure_ascii=False)
                logger.debug(f"Function response: {function_response}")  # 打印函数响应
            elif function_name == "get_datetime":
                function_args = json.loads(message["function_call"].get("arguments", "{}"))
                logger.debug(f"Function arguments: {function_args}")  # 打印函数参数
                city = function_args.get("city_en", "未指定城市")  # 如果没有指定城市，将默认查询北京
                function_response = fun.get_datetime(appkey=self.app_key, sign=self.app_sign, city_en=city)
                function_response = json.dumps(function_response, ensure_ascii=False)
                logger.debug(f"Function response: {function_response}")  # 打印函数响应
            elif function_name == "get_url":
                function_args = json.loads(message["function_call"].get("arguments", "{}"))
                logger.debug(f"Function arguments: {function_args}")  # 打印函数参数
                url = function_args.get("url", "未指定URL")
                function_response = fun.get_url(url=url)
                function_response = json.dumps(function_response, ensure_ascii=False)
                logger.debug(f"Function response: {function_response}")  # 打印函数响应
            elif function_name == "get_stock_info":
                function_args = json.loads(message["function_call"].get("arguments", "{}"))
                logger.debug(f"Function arguments: {function_args}")  # 打印函数参数
                stock_names = function_args.get("stock_names", "未指定股票信息")
                function_response = stock.get_stock_info(stock_names=stock_names, appkey=self.app_key,
                                                         sign=self.app_sign)
                function_response = json.dumps(function_response, ensure_ascii=False)
                logger.debug(f"Function response: {function_response}")  # 打印函数响应
            elif function_name == "get_video_url":
                function_args = json.loads(message["function_call"].get("arguments", "{}"))
                logger.debug(f"Function arguments: {function_args}")  # 打印函数参数
                url = function_args.get("url", "无URL")
                viedo_url = fun.get_video_url(api_key=self.alapi_key, target_url=url)
                if viedo_url:
                    logger.debug(f"viedo_url: {viedo_url}")
                    reply = Reply()  # 创建一个回复对象
                    reply.type = ReplyType.VIDEO_URL
                    reply.content = viedo_url
                    e_context["reply"] = reply
                    e_context.action = EventAction.BREAK_PASS
                    return
                else:
                    reply = Reply()  # 创建一个回复对象
                    reply.type = ReplyType.TEXT
                    reply.content = "抱歉，解析失败了·······"
                    e_context["reply"] = reply
                    e_context.action = EventAction.BREAK_PASS
                    return
            elif function_name == "search_bing_news":
                function_args = json.loads(message["function_call"].get("arguments", "{}"))
                logger.debug(f"Function arguments: {function_args}")  # 打印函数参数
                search_query = function_args.get("query", "未指定关键词")
                search_count = function_args.get("count", 10)
                function_response = fun.search_bing_news(count=search_count,
                                                         subscription_key=self.bing_subscription_key,
                                                         query=search_query, )
                function_response = json.dumps(function_response, ensure_ascii=False)
                logger.debug(f"Function response: {function_response}")  # 打印函数响应
            else:
                return

            msg: ChatMessage = e_context["context"]["msg"]
            current_date = datetime.now().strftime("%Y年%m月%d日%H时%M分")
            if e_context["context"]["isgroup"]:
                prompt = self.prompt.format(time=current_date, bot_name=msg.to_user_nickname,
                                                 name=msg.actual_user_nickname, content=content,
                                                 function_response=function_response)
            else:
                prompt = self.prompt.format(time=current_date, bot_name=msg.to_user_nickname,
                                                 name=msg.from_user_nickname, content=content,
                                                 function_response=function_response)
            # 将函数的返回结果发送给第二个模型
            logger.debug(f"prompt :" + prompt)
            # # content = context
            # # function_call = message["function_call"]
            # # function_call_str = json.dumps(function_call)
            # message_str = json.dumps(message)
            logger.debug("messages: %s", [{"role": "system", "content": prompt}])
            second_response = openai.ChatCompletion.create(
                model=self.assistant_openai_model,
                messages=[
                    {"role": "system", "content": prompt},
                ],
                temperature=float(self.temperature),
                max_tokens=int(self.max_tokens)
            )

            logger.debug(f"Second response: {second_response['choices'][0]['message']['content']}")  # 打印第二次的响应
            messages.append(second_response["choices"][0]["message"])
            return second_response['choices'][0]['message']['content']

        else:
            # 如果模型不希望调用函数，直接打印其响应
            logger.debug(f"Model response: {message['content']}")  # 打印模型的响应
            return

    def get_help_text(self, verbose=False, **kwargs):
        # 初始化帮助文本，说明利用 midjourney api 来画图
        help_text = "\n🔥GPT函数调用，极速联网，语境如需联网且有功能支持，则会直接联网获取实时信息\n"
        # 如果不需要详细说明，则直接返回帮助文本
        if not verbose:
            return help_text
        # 否则，添加详细的使用方法到帮助文本中
        help_text = "newgpt_turbo，极速联网无需特殊指令，前置识别\n🔎谷歌搜索、🔎新闻搜索\n🗞每日早报、☀全球天气\n⌚实时时间、⛽全国油价\n🌌星座运势、🎵音乐（网易云）\n🔥各类热榜信息、📹短视频解析等"
        # 返回帮助文本
        return help_text
