"""Optional interoperability check with the official MCP Python SDK v1.x.

Run after installing the SDK separately. No model calls or external requests.
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from shutil import copytree
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from web_growth_guard.marketing import read_growth_policy, growth_audit


async def main():
    with tempfile.TemporaryDirectory(prefix='wgg-sdk-') as temp:
        base = Path(temp)
        root = base / 'snapshot'
        copytree(Path(__file__).resolve().parent / 'growth', root)
        config = read_growth_policy(root)
        policy, baseline = base / 'policy.json', base / 'baseline.json'
        policy.write_text(json.dumps(config))
        baseline.write_text(json.dumps(growth_audit(root, config)))
        intelligence = base / 'intelligence.json'
        intelligence.write_text(json.dumps({'site':config['site'], 'entry_pages':[p['url'] for p in config['site']['pages']], 'max_depth':2, 'topics':[], 'required_links':[], 'schema_profiles':{}}))
        analytics = base / "analytics"
        copytree(Path(__file__).resolve().parent / "analytics", analytics)
        analytics_policy = base / "analytics.json"
        analytics_policy.write_bytes((analytics / "analytics.json").read_bytes())
        params = StdioServerParameters(command=sys.executable, args=[
            '-m', 'web_growth_guard.mcp_server', '--root', str(root), '--policy', str(policy), '--baseline', str(baseline), '--intelligence-policy', str(intelligence), '--analytics-root', str(analytics), '--analytics-policy', str(analytics_policy)])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                tools = await client.list_tools()
                assert {tool.name for tool in tools.tools} == {'list_landings', 'growth_audit', 'growth_gate', 'social_preview', 'campaign_url', 'campaign_batch', 'intelligence_audit', 'growth_plan', 'analytics_audit'}
                intelligence_report = await client.call_tool('intelligence_audit', {})
                assert not intelligence_report.isError and intelligence_report.structuredContent['eligible_verified'] == 2
                plan = await client.call_tool('growth_plan', {})
                assert not plan.isError and plan.structuredContent['format'] == 'wgg-growth-plan/v1'
                analytics_report = await client.call_tool('analytics_audit', {})
                assert not analytics_report.isError and analytics_report.structuredContent['format'] == 'wgg-analytics/v1'
                assert analytics_report.structuredContent['reports']['gsc_pages']['matched_current']['clicks'] == 30
                first = await client.call_tool('growth_gate', {})
                assert not first.isError and first.structuredContent['decision'] == 'CLEAR'
                page = root / 'pages' / 'vi.html'
                page.write_text(page.read_text().replace('href="/vi/join/"', 'href="/other/"'))
                second = await client.call_tool('growth_gate', {})
                assert not second.isError and second.structuredContent['decision'] == 'BLOCK'
                result = await client.call_tool('growth_audit', {'root': '/etc'})
                assert result.isError
                print('OFFICIAL SDK SMOKE PASS: nine tools, fresh CLEAR -> BLOCK, scoped input rejection')


if __name__ == '__main__':
    asyncio.run(asyncio.wait_for(main(), timeout=45))
