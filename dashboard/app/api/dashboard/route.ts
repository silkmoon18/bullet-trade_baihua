import { NextRequest, NextResponse } from 'next/server';
import { getChatGPTUser } from '../../chatgpt-auth';
import { loadDashboardData } from '../../dashboard-data';

export const dynamic = 'force-dynamic';

export async function GET(request: NextRequest) {
  const user = await getChatGPTUser();
  if (!user) return NextResponse.json({ error: 'unauthorized' }, { status: 401 });

  const strategyId = request.nextUrl.searchParams.get('strategy_id');
  try {
    const data = await loadDashboardData(strategyId);
    return NextResponse.json(data, {
      headers: { 'Cache-Control': 'no-store' },
    });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : '数据服务暂不可用' },
      { status: 502 },
    );
  }
}
