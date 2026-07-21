---
name: weather
description: 查询天气信息
category: query
risk_level: read_only
parameters:
  city:
    type: string
    description: 城市名，默认厦门
---
# 天气查询

当用户询问天气时使用此技能。

## 用法

调用 `weather(city="城市名")` 获取指定城市的天气。

## 实现说明

此技能使用 wttr.in 免费天气 API。
返回当前温度、天气状况、湿度和风速。

## 注意事项

- 城市名支持中文和英文
- 如用户未指定城市，默认查询厦门
