"use client";

import { Import, LoaderCircle, Pencil, Plus, RotateCcw, ServerCog, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { type CPAImportJob } from "@/lib/api";

import { useSettingsStore } from "../store";

function JobSummary({ title, job }: { title: string; job: CPAImportJob }) {
  const progress = job.total ? Math.round((job.completed / job.total) * 100) : 0;

  return (
    <div className="space-y-2 rounded-xl bg-stone-50 px-3 py-3">
      <div className="text-xs font-medium tracking-[0.16em] text-stone-400 uppercase">{title}</div>
      <div className="rounded-lg border border-stone-200 bg-white px-3 py-3">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="text-sm font-medium text-stone-700">
              状态 {job.status}，已处理 {job.completed}/{job.total}
            </div>
            <div className="truncate text-xs text-stone-400">
              任务 {job.job_id.slice(0, 8)} · {job.created_at}
            </div>
          </div>
          <Badge
            variant={job.status === "completed" ? "success" : job.status === "failed" ? "danger" : "info"}
            className="rounded-md"
          >
            {progress}%
          </Badge>
        </div>
        <div className="mt-3 h-2 overflow-hidden rounded-full bg-stone-200">
          <div className="h-full rounded-full bg-stone-900 transition-all" style={{ width: `${progress}%` }} />
        </div>
        <div className="mt-2 flex flex-wrap gap-2 text-xs text-stone-500">
          <span>新增 {job.added}</span>
          <span>跳过 {job.skipped}</span>
          <span>刷新 {job.refreshed}</span>
          {job.deleted > 0 ? <span>远端删除 {job.deleted}</span> : null}
          <span>失败 {job.failed}</span>
        </div>
      </div>
    </div>
  );
}

export function CPAPoolsCard() {
  const pools = useSettingsStore((state) => state.pools);
  const isLoadingPools = useSettingsStore((state) => state.isLoadingPools);
  const deletingId = useSettingsStore((state) => state.deletingId);
  const loadingFilesId = useSettingsStore((state) => state.loadingFilesId);
  const recoveringId = useSettingsStore((state) => state.recoveringId);
  const recoverLimit = useSettingsStore((state) => state.recoverLimit);
  const setRecoverLimit = useSettingsStore((state) => state.setRecoverLimit);
  const openAddDialog = useSettingsStore((state) => state.openAddDialog);
  const openEditDialog = useSettingsStore((state) => state.openEditDialog);
  const deletePool = useSettingsStore((state) => state.deletePool);
  const browseFiles = useSettingsStore((state) => state.browseFiles);
  const startRecoverExhausted = useSettingsStore((state) => state.startRecoverExhausted);

  return (
    <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
      <CardContent className="space-y-6 p-6">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-xl bg-stone-100">
              <ServerCog className="size-5 text-stone-600" />
            </div>
            <div>
              <h2 className="text-lg font-semibold tracking-tight">CPA 连接管理</h2>
              <p className="text-sm text-stone-500">支持手动选择导入，也支持一键导入 CPA 中已额度用完的账号。</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {pools.length > 0 ? <Badge className="rounded-md px-2.5 py-1">{pools.length} 个连接</Badge> : null}
            <div className="flex items-center gap-2 rounded-xl border border-stone-200 bg-white px-3 py-1.5">
              <span className="text-xs font-medium whitespace-nowrap text-stone-500">导入上限</span>
              <Input
                type="number"
                min="1"
                step="1"
                value={recoverLimit}
                onChange={(event) => setRecoverLimit(event.target.value)}
                className="h-8 w-[72px] border-0 bg-transparent px-0 text-center text-sm font-medium text-stone-700 shadow-none focus-visible:ring-0"
              />
            </div>
            <Button className="h-9 rounded-xl bg-stone-950 px-4 text-white hover:bg-stone-800" onClick={openAddDialog}>
              <Plus className="size-4" />
              添加连接
            </Button>
          </div>
        </div>

        {isLoadingPools ? (
          <div className="flex items-center justify-center py-10">
            <LoaderCircle className="size-5 animate-spin text-stone-400" />
          </div>
        ) : pools.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-3 rounded-xl bg-stone-50 px-6 py-10 text-center">
            <ServerCog className="size-8 text-stone-300" />
            <div className="space-y-1">
              <p className="text-sm font-medium text-stone-600">暂无 CPA 连接</p>
              <p className="text-sm text-stone-400">点击「添加连接」保存你的 CLIProxyAPI 信息。</p>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            {pools.map((pool) => {
              const hasRunningJob =
                pool.import_job?.status === "pending" ||
                pool.import_job?.status === "running" ||
                pool.recover_job?.status === "pending" ||
                pool.recover_job?.status === "running";
              const isBusy =
                hasRunningJob ||
                deletingId === pool.id ||
                loadingFilesId === pool.id ||
                recoveringId === pool.id;

              return (
                <div key={pool.id} className="flex flex-col gap-3 rounded-xl border border-stone-200 bg-white px-4 py-3">
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <div className="text-sm font-medium text-stone-800">{pool.name || pool.base_url}</div>
                      <div className="truncate text-xs text-stone-400">{pool.base_url}</div>
                    </div>
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        className="rounded-lg p-2 text-stone-400 transition hover:bg-stone-100 hover:text-stone-700"
                        onClick={() => openEditDialog(pool)}
                        disabled={isBusy}
                        title="编辑"
                      >
                        <Pencil className="size-4" />
                      </button>
                      <button
                        type="button"
                        className="rounded-lg p-2 text-stone-400 transition hover:bg-rose-50 hover:text-rose-500"
                        onClick={() => void deletePool(pool)}
                        disabled={isBusy}
                        title="删除"
                      >
                        {deletingId === pool.id ? (
                          <LoaderCircle className="size-4 animate-spin" />
                        ) : (
                          <Trash2 className="size-4" />
                        )}
                      </button>
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      variant="outline"
                      className="h-8 rounded-lg border-stone-200 bg-white px-3 text-xs text-stone-600"
                      onClick={() => void browseFiles(pool)}
                      disabled={isBusy}
                    >
                      {loadingFilesId === pool.id ? (
                        <LoaderCircle className="size-3.5 animate-spin" />
                      ) : (
                        <Import className="size-3.5" />
                      )}
                      手动导入
                    </Button>
                    <Button
                      variant="outline"
                      className="h-8 rounded-lg border-stone-200 bg-white px-3 text-xs text-stone-600"
                      onClick={() => void startRecoverExhausted(pool)}
                      disabled={isBusy}
                    >
                      {recoveringId === pool.id ? (
                        <LoaderCircle className="size-3.5 animate-spin" />
                      ) : (
                        <RotateCcw className="size-3.5" />
                      )}
                      导入额度用完
                    </Button>
                  </div>

                  {pool.import_job ? <JobSummary title="手动导入任务" job={pool.import_job} /> : null}
                  {pool.recover_job ? <JobSummary title="额度用完导入任务" job={pool.recover_job} /> : null}
                </div>
              );
            })}
          </div>
        )}

        <div className="rounded-xl bg-stone-50 px-4 py-3 text-sm leading-6 text-stone-500">
          <p className="font-medium text-stone-600">使用说明</p>
          <ul className="mt-1 list-inside list-disc space-y-0.5">
            <li>手动导入会先读取远程账号列表，再由你选择需要导入的账号。</li>
            <li>额度用完导入会自动筛选 CPA 中已用尽额度的账号，例如 `usage_limit_reached`。</li>
            <li>区块顶部的导入上限默认是 50，本次任务只会导入命中的前 N 个账号。</li>
            <li>该任务只会把账号导入本地号池，不会删除 CPA 远端文件。</li>
            <li>同一个 CPA 连接同一时间只允许执行一个任务，避免多个导入任务相互冲突。</li>
          </ul>
        </div>
      </CardContent>
    </Card>
  );
}
