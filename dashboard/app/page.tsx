import DashboardClient from './dashboard-client';
import { requireChatGPTUser } from './chatgpt-auth';
import { loadDashboardData, unavailableDashboardData } from './dashboard-data';

export const dynamic = 'force-dynamic';

export default async function Home() {
  const user = await requireChatGPTUser('/');
  let initialData;
  try {
    initialData = await loadDashboardData();
  } catch (error) {
    initialData = unavailableDashboardData(
      error instanceof Error ? error.message : '数据服务暂不可用',
    );
  }

  return (
    <DashboardClient
      initialData={initialData}
      user={{ displayName: user.displayName, email: user.email }}
    />
  );
}
