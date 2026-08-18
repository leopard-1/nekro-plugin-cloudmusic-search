# 网易云点歌插件

Nekro-Agent 网易云音乐点歌插件，支持歌曲搜索、歌手相关搜索、专辑查询、可选音质播放。

## 功能

- 搜索网易云歌曲
- 搜索歌曲默认最多返回 20 条，并附带编号和歌曲 ID
- 按歌手搜索相关歌曲和专辑，最多各 20 条
- 获取专辑详情和专辑歌曲列表
- 配置默认音质，用户命令或会话里指定音质时优先
- 搜索结果支持文本或图片输出
- OneBot v11 下支持 JSON 音乐卡片，失败后降级为文字、封面和语音
- 支持手机号验证码登录和二维码登录，并自动写入 `NCM_COOKIE`

## 配置

- `NCM_COOKIE`：网易云音乐 Cookie，建议包含 `MUSIC_U` 和 `__csrf`
- `DEFAULT_QUALITY`：默认音质，支持 `standard`、`higher`、`exhigh`、`lossless`、`hires`
- `SEARCH_OUTPUT_MODE`：搜索结果输出模式，`text` 或 `image`
- `IMAGE_BACKGROUND_URL`：搜索结果图片背景 URL
- `IMAGE_WIDTH`：搜索结果图片宽度，单位像素
- `IMAGE_HEIGHT`：搜索结果图片高度，单位像素，填 `0` 时自动计算
- `IMAGE_INDEX_COLOR`：image 模式下序号字体色号，例如 `#ff3850`
- `IMAGE_SONG_NAME_COLOR`：image 模式下标题和歌名字体色号，例如 `#ffffff`
- `FONT_PATH`：图片字体路径，支持 ttf/ttc
- `MAX_SEARCH_RESULTS`：搜索结果数量，1-20
- `DEFAULT_COVER_URL`：默认封面 URL
- `HTTP_TIMEOUT`：HTTP 超时时间
- `ENABLE_JSON_CARD`：是否启用 OneBot JSON 音乐卡片
- `CARD_FALLBACK_MODE`：卡片失败时的降级模式，`voice`、`text` 或 `none`
- `COVER_SIZE`：封面尺寸，0 表示不发送封面
- `PLAY_DEDUP_SECONDS`：播放请求去重秒数，默认 `12`，填 `0` 可关闭

## 命令

```text
/cm_help
/cm_search 晴天
/cm_search 晴天 image
/cm_play 1
/cm_artist 周杰伦
/cm_artist 陈奕迅 text
/cm_album 35327877
/cm_album 叶惠美 image
/cm_play 186016 standard
/cm_play 186016 exhigh
/cm_play 186016 lossless
/cm_login -phone 13800138000
/cm_login -qr
```

## 音质

支持以下音质：

- `standard`：标准
- `higher`：较高
- `exhigh`：极高
- `lossless`：无损
- `hires`：Hi-Res

命令里的音质优先于配置项。例如 `DEFAULT_QUALITY=lossless` 时，用户发送 `/cm_play 186016 standard` 会按 `standard` 请求。

## 编号选择

`/cm_search`、`/cm_artist` 和 `/cm_album` 返回歌曲列表后，会缓存最近一次搜索的歌曲编号。随后可以直接使用：

```text
/cm_play 1
```

如果输入大于 20 的数字，会按网易云歌曲 ID 处理。

## 卡片发送

OneBot v11 下插件会优先尝试发送网易云 JSON 卡片。若变成语音消息，通常是以下原因：

- 外部 JSON 卡片签名接口不可用或返回失败
- OneBot 实现不支持发送 JSON 卡片
- 账号或平台风控拦截 JSON 卡片
- 插件配置关闭了 `ENABLE_JSON_CARD`

可以通过 `CARD_FALLBACK_MODE` 控制卡片失败后的行为：

- `voice`：默认，降级发送文字、封面和语音
- `text`：只发送文字链接
- `none`：卡片失败后不降级发送

## 字体

图片模式需要可用中文字体。推荐把字体放到插件目录：

```text
fonts/font.ttf
```

然后将 `FONT_PATH` 配置为：

```text
fonts/font.ttf
```

## 说明

插件直接使用 `https://apis.netstart.cn/music` 提供的网易云音乐 API，不再安装 `pyncm` 或 `NeteaseCloudMusic` 动态依赖。本插件仅供学习交流使用，请遵守音乐版权相关法律法规。
