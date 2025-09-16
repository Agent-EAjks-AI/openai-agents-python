---
search:
  exclude: true
---
# エージェント

エージェントは、アプリの中核となる基本コンポーネントです。エージェントは、instructions とツールで構成された大規模言語モデル ( LLM ) です。

## 基本設定

一般的に設定するエージェントの主なプロパティは次のとおりです。

- `name`: エージェントを識別する必須の文字列。
- `instructions`: developer メッセージ、または system prompt とも呼ばれます。
- `model`: 使用する LLM、および temperature、top_p などのモデル調整パラメーターを設定する任意の `model_settings`。
- `tools`: エージェントがタスク達成のために使用できるツール。

```python
from agents import Agent, ModelSettings, function_tool

@function_tool
def get_weather(city: str) -> str:
    """returns weather info for the specified city."""
    return f"The weather in {city} is sunny"

agent = Agent(
    name="Haiku agent",
    instructions="Always respond in haiku form",
    model="o3-mini",
    tools=[get_weather],
)
```

## コンテキスト

エージェントはその `context` 型に対してジェネリックです。コンテキストは依存性注入ツールで、あなたが作成して `Runner.run()` に渡すオブジェクトです。これはすべてのエージェント、ツール、ハンドオフなどに渡され、エージェント実行のための依存関係と状態のまとめ役として機能します。コンテキストには任意の Python オブジェクトを提供できます。

```python
@dataclass
class UserContext:
    name: str
    uid: str
    is_pro_user: bool

    async def fetch_purchases() -> list[Purchase]:
        return ...

agent = Agent[UserContext](
    ...,
)
```

## 出力タイプ

デフォルトでは、エージェントはプレーンテキスト (すなわち `str`) を出力します。特定の型の出力をエージェントに生成させたい場合は、`output_type` パラメーターを使用できます。一般的な選択肢は [Pydantic](https://docs.pydantic.dev/) オブジェクトですが、Pydantic の [TypeAdapter](https://docs.pydantic.dev/latest/api/type_adapter/) でラップできる任意の型 (dataclasses、lists、TypedDict など) をサポートします。

```python
from pydantic import BaseModel
from agents import Agent


class CalendarEvent(BaseModel):
    name: str
    date: str
    participants: list[str]

agent = Agent(
    name="Calendar extractor",
    instructions="Extract calendar events from text",
    output_type=CalendarEvent,
)
```

!!! note

    `output_type` を渡すと、モデルは通常のプレーンテキスト応答の代わりに [structured outputs](https://platform.openai.com/docs/guides/structured-outputs) を使用するよう指示されます。

## マルチ エージェント システムの設計パターン

マルチ エージェント システムの設計にはさまざまな方法がありますが、一般的に広く適用できるパターンとして次の 2 つがよく見られます。

1. マネージャー (エージェントをツールとして使用): 中央のマネージャー／オーケストレーターが、ツールとして公開された専門のサブエージェントを呼び出し、会話の制御を維持します。
2. ハンドオフ: ピアのエージェントが、会話を引き継ぐ専門エージェントに制御を引き渡します。こちらは分散型です。

詳細は [実践的なエージェント構築ガイド](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf) を参照してください。

### マネージャー (エージェントをツールとして使用)

`customer_facing_agent` はすべてのユーザー対応を処理し、ツールとして公開された専門のサブエージェントを呼び出します。詳細は [ツール](tools.md#agents-as-tools) ドキュメントを参照してください。

```python
from agents import Agent

booking_agent = Agent(...)
refund_agent = Agent(...)

customer_facing_agent = Agent(
    name="Customer-facing agent",
    instructions=(
        "Handle all direct user communication. "
        "Call the relevant tools when specialized expertise is needed."
    ),
    tools=[
        booking_agent.as_tool(
            tool_name="booking_expert",
            tool_description="Handles booking questions and requests.",
        ),
        refund_agent.as_tool(
            tool_name="refund_expert",
            tool_description="Handles refund questions and requests.",
        )
    ],
)
```

### ハンドオフ

ハンドオフは、エージェントが委譲できるサブエージェントです。ハンドオフが発生すると、委譲先エージェントは会話履歴を受け取り、会話を引き継ぎます。このパターンにより、単一のタスクに特化して優れた性能を発揮する、モジュール化された専門エージェントが実現します。詳細は [ハンドオフ](handoffs.md) ドキュメントを参照してください。

```python
from agents import Agent

booking_agent = Agent(...)
refund_agent = Agent(...)

triage_agent = Agent(
    name="Triage agent",
    instructions=(
        "Help the user with their questions. "
        "If they ask about booking, hand off to the booking agent. "
        "If they ask about refunds, hand off to the refund agent."
    ),
    handoffs=[booking_agent, refund_agent],
)
```

## 動的 instructions

多くの場合、エージェント作成時に instructions を指定できます。しかし、関数を介して動的な instructions を提供することも可能です。この関数はエージェントとコンテキストを受け取り、プロンプトを返す必要があります。通常の関数と `async` 関数の両方が使用できます。

```python
def dynamic_instructions(
    context: RunContextWrapper[UserContext], agent: Agent[UserContext]
) -> str:
    return f"The user's name is {context.context.name}. Help them with their questions."


agent = Agent[UserContext](
    name="Triage agent",
    instructions=dynamic_instructions,
)
```

## ライフサイクルイベント (フック)

エージェントのライフサイクルを観測したい場合があります。たとえば、イベントをログに記録したり、特定のイベント発生時にデータを事前取得したりするケースです。`hooks` プロパティでエージェントのライフサイクルにフックできます。[`AgentHooks`][agents.lifecycle.AgentHooks] クラスをサブクラス化し、関心のあるメソッドをオーバーライドしてください。

## ガードレール

ガードレールにより、エージェントの実行と並行してユーザー入力に対するチェック／検証を行い、出力生成後にはエージェントの出力に対するチェック／検証を実行できます。たとえば、ユーザーの入力やエージェントの出力の関連性をスクリーニングできます。詳細は [ガードレール](guardrails.md) ドキュメントを参照してください。

## エージェントのクローン／コピー

エージェントの `clone()` メソッドを使うと、エージェントを複製し、必要に応じて任意のプロパティを変更できます。

```python
pirate_agent = Agent(
    name="Pirate",
    instructions="Write like a pirate",
    model="o3-mini",
)

robot_agent = pirate_agent.clone(
    name="Robot",
    instructions="Write like a robot",
)
```

## ツール使用の強制

ツールのリストを指定しても、LLM が必ずしもツールを使用するとは限りません。[`ModelSettings.tool_choice`][agents.model_settings.ModelSettings.tool_choice] を設定することで、ツール使用を強制できます。有効な値は次のとおりです。

1. `auto`: LLM にツールを使うかどうかの判断を委ねます。
2. `required`: LLM にツールの使用を必須にします (ただし、どのツールを使うかは賢く判断できます)。
3. `none`: LLM にツールを _使わない_ ことを要求します。
4. 特定の文字列を設定 (例: `my_tool`) すると、LLM にその特定のツールの使用を要求します。

```python
from agents import Agent, Runner, function_tool, ModelSettings

@function_tool
def get_weather(city: str) -> str:
    """Returns weather info for the specified city."""
    return f"The weather in {city} is sunny"

agent = Agent(
    name="Weather Agent",
    instructions="Retrieve weather details.",
    tools=[get_weather],
    model_settings=ModelSettings(tool_choice="get_weather")
)
```

## ツール使用の挙動

`Agent` 構成の `tool_use_behavior` パラメーターは、ツール出力の扱い方を制御します。

- `"run_llm_again"`: デフォルト。ツールを実行し、その結果を LLM が処理して最終応答を生成します。
- `"stop_on_first_tool"`: 最初のツール呼び出しの出力を、追加の LLM 処理なしに最終応答として使用します。

```python
from agents import Agent, Runner, function_tool, ModelSettings

@function_tool
def get_weather(city: str) -> str:
    """Returns weather info for the specified city."""
    return f"The weather in {city} is sunny"

agent = Agent(
    name="Weather Agent",
    instructions="Retrieve weather details.",
    tools=[get_weather],
    tool_use_behavior="stop_on_first_tool"
)
```

- `StopAtTools(stop_at_tool_names=[...])`: 指定したいずれかのツールが呼び出された場合に停止し、その出力を最終応答として使用します。

```python
from agents import Agent, Runner, function_tool
from agents.agent import StopAtTools

@function_tool
def get_weather(city: str) -> str:
    """Returns weather info for the specified city."""
    return f"The weather in {city} is sunny"

@function_tool
def sum_numbers(a: int, b: int) -> int:
    """Adds two numbers."""
    return a + b

agent = Agent(
    name="Stop At Stock Agent",
    instructions="Get weather or sum numbers.",
    tools=[get_weather, sum_numbers],
    tool_use_behavior=StopAtTools(stop_at_tool_names=["get_weather"])
)
```

- `ToolsToFinalOutputFunction`: ツール結果を処理し、停止するか LLM を継続するかを決定するカスタム関数です。

```python
from agents import Agent, Runner, function_tool, FunctionToolResult, RunContextWrapper
from agents.agent import ToolsToFinalOutputResult
from typing import List, Any

@function_tool
def get_weather(city: str) -> str:
    """Returns weather info for the specified city."""
    return f"The weather in {city} is sunny"

def custom_tool_handler(
    context: RunContextWrapper[Any],
    tool_results: List[FunctionToolResult]
) -> ToolsToFinalOutputResult:
    """Processes tool results to decide final output."""
    for result in tool_results:
        if result.output and "sunny" in result.output:
            return ToolsToFinalOutputResult(
                is_final_output=True,
                final_output=f"Final weather: {result.output}"
            )
    return ToolsToFinalOutputResult(
        is_final_output=False,
        final_output=None
    )

agent = Agent(
    name="Weather Agent",
    instructions="Retrieve weather details.",
    tools=[get_weather],
    tool_use_behavior=custom_tool_handler
)
```

!!! note

    無限ループを防ぐため、フレームワークはツール呼び出し後に `tool_choice` を自動的に "auto" にリセットします。この挙動は [`agent.reset_tool_choice`][agents.agent.Agent.reset_tool_choice] で設定できます。無限ループは、ツール結果が LLM に送られ、その後 `tool_choice` によって LLM がさらに別のツール呼び出しを生成し続けるために発生します。