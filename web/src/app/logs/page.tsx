"use client";

import { useEffect, useMemo, useState } from "react";
import {
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  LoaderCircle,
  RefreshCw,
  ShieldAlert,
  SquareTerminal,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { fetchRequestLogs, type RequestLogItem } from "@/lib/api";

const PAGE_SIZE_OPTIONS = [20, 50, 100];

function formatTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value || "—";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function formatDuration(value: number) {
  if (value < 1000) {
    return `${value} ms`;
  }
  return `${(value / 1000).toFixed(2)} s`;
}

function formatValue(value: unknown) {
  if (value === null || value === undefined || value === "") {
    return "—";
  }
  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }
  if (typeof value === "number") {
    return String(value);
  }
  if (typeof value === "string") {
    return value;
  }
  if (Array.isArray(value)) {
    return value.map((item) => formatValue(item)).join(" / ");
  }
  return JSON.stringify(value);
}

function getSummaryEntries(summary: Record<string, unknown>) {
  return Object.entries(summary).filter(([, value]) => {
    if (value === null || value === undefined || value === "") {
      return false;
    }
    if (Array.isArray(value) && value.length === 0) {
      return false;
    }
    return true;
  });
}

function getPromptPreview(item: RequestLogItem) {
  const value = item.request["prompt_preview"];
  return typeof value === "string" && value.trim() ? value : "—";
}

function getStatusVariant(item: RequestLogItem) {
  if (item.success) {
    return "success" as const;
  }
  if (item.status_code >= 500) {
    return "danger" as const;
  }
  if (item.status_code >= 400) {
    return "warning" as const;
  }
  return "secondary" as const;
}

function getStatusText(item: RequestLogItem) {
  if (item.success) {
    return "成功";
  }
  if (item.status_code > 0) {
    return `失败 ${item.status_code}`;
  }
  return "失败";
}

export default function LogsPage() {
  const [items, setItems] = useState<RequestLogItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);

  const loadLogs = async ({ silent = false } = {}) => {
    if (silent) {
      setIsRefreshing(true);
    } else {
      setIsLoading(true);
    }

    try {
      const data = await fetchRequestLogs(page, pageSize);
      setItems(data.items);
      setTotal(data.total);
    } catch (error) {
      const message = error instanceof Error ? error.message : "加载请求日志失败";
      toast.error(message);
    } finally {
      if (silent) {
        setIsRefreshing(false);
      } else {
        setIsLoading(false);
      }
    }
  };

  useEffect(() => {
    void loadLogs();
  }, [page, pageSize]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const summary = useMemo(() => {
    const success = items.filter((item) => item.success).length;
    const failed = items.length - success;
    return { success, failed };
  }, [items]);

  return (
    <section className="space-y-5">
      <div className="grid gap-4 md:grid-cols-4">
        <Card className="border-white/80 bg-white/80 backdrop-blur">
          <CardContent className="flex items-center gap-4 p-5">
            <div className="rounded-2xl bg-stone-100 p-3 text-stone-700">
              <SquareTerminal className="size-5" />
            </div>
            <div>
              <div className="text-xs font-medium tracking-[0.18em] text-stone-400 uppercase">总日志数</div>
              <div className="mt-1 text-2xl font-semibold text-stone-950">{total}</div>
            </div>
          </CardContent>
        </Card>
        <Card className="border-white/80 bg-white/80 backdrop-blur">
          <CardContent className="flex items-center gap-4 p-5">
            <div className="rounded-2xl bg-emerald-50 p-3 text-emerald-600">
              <CheckCircle2 className="size-5" />
            </div>
            <div>
              <div className="text-xs font-medium tracking-[0.18em] text-stone-400 uppercase">本页成功</div>
              <div className="mt-1 text-2xl font-semibold text-stone-950">{summary.success}</div>
            </div>
          </CardContent>
        </Card>
        <Card className="border-white/80 bg-white/80 backdrop-blur">
          <CardContent className="flex items-center gap-4 p-5">
            <div className="rounded-2xl bg-rose-50 p-3 text-rose-600">
              <ShieldAlert className="size-5" />
            </div>
            <div>
              <div className="text-xs font-medium tracking-[0.18em] text-stone-400 uppercase">本页失败</div>
              <div className="mt-1 text-2xl font-semibold text-stone-950">{summary.failed}</div>
            </div>
          </CardContent>
        </Card>
        <Card className="border-white/80 bg-white/80 backdrop-blur">
          <CardContent className="flex items-center gap-4 p-5">
            <div className="rounded-2xl bg-sky-50 p-3 text-sky-600">
              <Clock3 className="size-5" />
            </div>
            <div>
              <div className="text-xs font-medium tracking-[0.18em] text-stone-400 uppercase">当前页</div>
              <div className="mt-1 text-2xl font-semibold text-stone-950">
                {page}/{totalPages}
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card className="border-white/80 bg-white/80 backdrop-blur">
        <CardHeader className="gap-4 md:flex-row md:items-center md:justify-between">
          <div>
            <CardTitle>请求日志</CardTitle>
            <CardDescription className="mt-2">
              只保留最近 2000 条请求日志，只记录请求摘要与响应摘要，不保存图片二进制或 `b64_json`。
            </CardDescription>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-2 rounded-full border border-stone-200 bg-stone-50 px-3 py-2 text-sm text-stone-600">
              <span>每页</span>
              <select
                value={pageSize}
                className="bg-transparent text-sm text-stone-900 outline-none"
                onChange={(event) => {
                  setPageSize(Number(event.target.value));
                  setPage(1);
                }}
              >
                {PAGE_SIZE_OPTIONS.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
            <Button variant="outline" onClick={() => void loadLogs({ silent: true })} disabled={isRefreshing}>
              <RefreshCw className={`size-4 ${isRefreshing ? "animate-spin" : ""}`} />
              刷新
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {isLoading ? (
            <div className="flex min-h-64 items-center justify-center text-stone-500">
              <LoaderCircle className="size-5 animate-spin" />
            </div>
          ) : items.length === 0 ? (
            <div className="rounded-3xl border border-dashed border-stone-200 bg-stone-50/80 px-6 py-12 text-center text-sm text-stone-500">
              还没有请求日志。
            </div>
          ) : (
            <div className="space-y-3">
              {items.map((item) => {
                const requestSummary = getSummaryEntries(item.request);
                const responseSummary = getSummaryEntries(item.response);

                return (
                  <article
                    key={item.request_id || `${item.created_at}-${item.endpoint}`}
                    className="space-y-4 rounded-3xl border border-stone-200/80 bg-white px-5 py-5 shadow-[0_12px_30px_-24px_rgba(15,23,42,0.35)]"
                  >
                    <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
                      <div className="space-y-3">
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge variant="outline">{item.method}</Badge>
                          <Badge variant={getStatusVariant(item)}>{getStatusText(item)}</Badge>
                          {item.model ? <Badge variant="info">{item.model}</Badge> : null}
                          <span className="text-xs text-stone-400">{formatTime(item.created_at)}</span>
                        </div>
                        <div className="text-base font-semibold tracking-tight text-stone-950">{item.endpoint}</div>
                        <p className="max-w-4xl break-all text-sm leading-6 text-stone-600">{getPromptPreview(item)}</p>
                        {item.error ? (
                          <div className="rounded-2xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs leading-5 text-rose-700">
                            {item.error}
                          </div>
                        ) : null}
                      </div>
                      <div className="grid gap-2 sm:grid-cols-2 xl:min-w-[360px]">
                        <div className="rounded-2xl bg-stone-50 px-3 py-3">
                          <div className="text-[11px] font-medium tracking-[0.16em] text-stone-400 uppercase">
                            耗时
                          </div>
                          <div className="mt-1 text-sm font-medium text-stone-900">{formatDuration(item.duration_ms)}</div>
                        </div>
                        <div className="rounded-2xl bg-stone-50 px-3 py-3">
                          <div className="text-[11px] font-medium tracking-[0.16em] text-stone-400 uppercase">IP</div>
                          <div className="mt-1 break-all text-sm font-medium text-stone-900">
                            {item.client_ip || "—"}
                          </div>
                        </div>
                        <div className="rounded-2xl bg-stone-50 px-3 py-3 sm:col-span-2">
                          <div className="text-[11px] font-medium tracking-[0.16em] text-stone-400 uppercase">
                            请求 ID
                          </div>
                          <div className="mt-1 break-all font-mono text-xs text-stone-700">
                            {item.request_id || "—"}
                          </div>
                        </div>
                        <div className="rounded-2xl bg-stone-50 px-3 py-3 sm:col-span-2">
                          <div className="text-[11px] font-medium tracking-[0.16em] text-stone-400 uppercase">
                            User-Agent
                          </div>
                          <div className="mt-1 break-all text-xs leading-5 text-stone-700">{item.user_agent || "—"}</div>
                        </div>
                      </div>
                    </div>

                    <div className="grid gap-3 lg:grid-cols-2">
                      <div className="rounded-3xl bg-stone-50/90 px-4 py-4">
                        <div className="text-sm font-semibold text-stone-900">请求摘要</div>
                        <div className="mt-3 flex flex-wrap gap-2">
                          {requestSummary.length > 0 ? (
                            requestSummary.map(([key, value]) => (
                              <Badge key={key} variant="secondary" className="max-w-full break-all py-1 leading-5">
                                {key}: {formatValue(value)}
                              </Badge>
                            ))
                          ) : (
                            <span className="text-sm text-stone-400">无</span>
                          )}
                        </div>
                      </div>
                      <div className="rounded-3xl bg-stone-50/90 px-4 py-4">
                        <div className="text-sm font-semibold text-stone-900">响应摘要</div>
                        <div className="mt-3 flex flex-wrap gap-2">
                          {responseSummary.length > 0 ? (
                            responseSummary.map(([key, value]) => (
                              <Badge key={key} variant="secondary" className="max-w-full break-all py-1 leading-5">
                                {key}: {formatValue(value)}
                              </Badge>
                            ))
                          ) : (
                            <span className="text-sm text-stone-400">无</span>
                          )}
                        </div>
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
          )}

          <div className="flex flex-col gap-3 border-t border-stone-200/80 pt-2 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-sm text-stone-500">
              共 {total} 条，当前第 {page} 页。
            </div>
            <div className="flex items-center gap-2">
              <Button variant="outline" disabled={page <= 1} onClick={() => setPage((current) => Math.max(1, current - 1))}>
                <ChevronLeft className="size-4" />
                上一页
              </Button>
              <Button
                variant="outline"
                disabled={page >= totalPages}
                onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
              >
                下一页
                <ChevronRight className="size-4" />
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </section>
  );
}
