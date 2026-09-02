#!/usr/bin/env python3
"""Phase 36.1 tests."""
import sys
sys.path.insert(0, '.')

from app.ai.conversation_engine.context_builder import ConversationContext
from app.ai.conversation_engine.response_generator import ResponseGenerator, GenerationResult
from app.ai.conversation_engine.tool_calling import ToolExecutor, ToolCall
from app.ai.conversation_engine.prompt_builder import build_messages, build_system_prompt, build_context_layer, TONE_MODIFIERS, STATE_BEHAVIORS

passed = 0
failed = 0

def test(name, fn):
    global passed, failed
    try:
        fn()
        passed += 1
        print(f'  PASS: {name}')
    except Exception as e:
        failed += 1
        print(f'  FAIL: {name}: {e}')

def t1():
    ctx = ConversationContext()
    d = ctx.to_dict()
    assert 'lead_id' in d
    assert 'sentiment' in d
test('ConversationContext', t1)

def t2():
    gr = GenerationResult(response='Hi', model_used='llama3', tokens_used=5, was_validated=True)
    d = gr.to_dict()
    assert d['response'] == 'Hi'
    assert d['model_used'] == 'llama3'
test('GenerationResult', t2)

def t3():
    tc = ToolCall('search_slots', {'date': '2026-05-23'})
    assert tc.tool_name == 'search_slots'
    d = tc.to_dict()
    assert d['tool'] == 'search_slots'
test('ToolCall', t3)

def t4():
    ctx = ConversationContext()
    msgs = build_messages(ctx, 'Hello', include_tools=False)
    assert len(msgs) >= 2
    assert msgs[0]['role'] == 'system'
    assert msgs[-1]['role'] == 'user'
    assert msgs[-1]['content'] == 'Hello'
test('Prompt builder', t4)

def t5():
    ctx = ConversationContext()
    prompt = build_system_prompt(ctx, include_tools=True)
    assert 'search_slots' in prompt
    assert 'book_appointment' in prompt
test('System prompt with tools', t5)

def t6():
    ctx = ConversationContext()
    cl = build_context_layer(ctx)
    assert cl == ''
test('Empty context layer', t6)

def t7():
    assert len(TONE_MODIFIERS) >= 4
    assert 'friendly' in TONE_MODIFIERS
    assert 'professional' in TONE_MODIFIERS
test('Tone modifiers', t7)

def t8():
    assert len(STATE_BEHAVIORS) >= 9
    assert 'new_lead' in STATE_BEHAVIORS
    assert 'booked' in STATE_BEHAVIORS
test('State behaviors', t8)

def t9():
    rg = ResponseGenerator()
    assert rg.max_tokens == 500
    assert rg.temperature == 0.7
    fallback = rg._get_safe_fallback('friendly')
    assert len(fallback) > 20
test('ResponseGenerator', t9)

def make_executor():
    """Create a ToolExecutor with mocked handlers for testing."""
    executor = ToolExecutor.__new__(ToolExecutor)
    executor._handlers = {
        "search_slots": None,
        "book_appointment": None,
        "reschedule": None,
        "cancel_appointment": None,
        "update_lead": None,
        "add_to_suppression": None,
        "escalate_to_agent": None,
        "move_to_nurture": None,
    }
    return executor

def t10():
    executor = make_executor()
    resp = 'Sure! ```tool\n{"tool": "search_slots", "args": {"date": "2026-05-23"}}\n```'
    calls = executor.parse_tool_calls(resp)
    assert len(calls) == 1
    assert calls[0].tool_name == 'search_slots'
    assert calls[0].args['date'] == '2026-05-23'
test('Tool parsing', t10)

def t11():
    executor = make_executor()
    calls = executor.parse_tool_calls('Thanks for reaching out!')
    assert len(calls) == 0
test('No tool calls', t11)

def t12():
    ctx = ConversationContext()
    ctx.objections = [{'type': 'pricing', 'text': 'too expensive'}]
    ctx.sentiment = {'current': 'positive', 'score': 0.8, 'trend': 'improving'}
    ctx.preferences = {'time': 'afternoon'}
    msgs = build_messages(ctx, 'Hello', include_tools=True)
    assert len(msgs) >= 3
test('Rich context', t12)

def t13():
    executor = make_executor()
    resp = '''Let me help.

```tool
{"tool": "search_slots", "args": {"date": "2026-05-23"}}
```

And also:

```tool
{"tool": "update_lead", "args": {"field": "email", "value": "test@test.com"}}
```'''
    calls = executor.parse_tool_calls(resp)
    assert len(calls) == 2
    assert calls[0].tool_name == 'search_slots'
    assert calls[1].tool_name == 'update_lead'
test('Multiple tool calls', t13)

def t14():
    ctx = ConversationContext()
    ctx.conversation_state = 'interested'
    ctx.campaign = type('obj', (object,), {'tone': 'professional', 'prompt_template': None, 'objection_prompts': None})()
    prompt = build_system_prompt(ctx, include_tools=False)
    assert 'professional' in prompt.lower() or 'TONE' in prompt
test('Tone in prompt', t14)

def t15():
    from app.ai.conversation_engine.engine import ConversationResponse
    cr = ConversationResponse(
        message='Hello!',
        intent='INTERESTED',
        intent_confidence=0.9,
        sentiment='positive',
        sentiment_score=0.8,
    )
    d = cr.to_dict()
    assert d['message'] == 'Hello!'
    assert d['intent']['type'] == 'INTERESTED'
    assert d['should_book'] == False
test('ConversationResponse', t15)

print()
print(f'Results: {passed} passed, {failed} failed out of {passed + failed}')
if failed > 0:
    sys.exit(1)
print('ALL TESTS PASSED')
