"use client";

import { create } from "zustand";
import { toast } from "sonner";

import {
  createCPAPool,
  deleteCPAPool,
  fetchCPAPoolFiles,
  fetchCPAPools,
  fetchSettingsConfig,
  startCPAImport,
  startCPARecover401,
  updateCPAPool,
  updateSettingsConfig,
  type CPAPool,
  type CPARemoteFile,
  type SettingsConfig,
} from "@/lib/api";

export const PAGE_SIZE_OPTIONS = ["50", "100", "200"] as const;

export type PageSizeOption = (typeof PAGE_SIZE_OPTIONS)[number];

function normalizeConfig(config: SettingsConfig): SettingsConfig {
  const rawNewapi: NonNullable<SettingsConfig["newapi"]> =
    config.newapi && typeof config.newapi === "object" ? config.newapi : {};
  return {
    ...config,
    "auth-key": typeof config["auth-key"] === "string" ? config["auth-key"] : "",
    "admin-password": typeof config["admin-password"] === "string" ? config["admin-password"] : "",
    refresh_account_interval_minute: Number(config.refresh_account_interval_minute || 5),
    proxy: typeof config.proxy === "string" ? config.proxy : "",
    base_url: typeof config.base_url === "string" ? config.base_url : "",
    newapi: {
      enabled: Boolean(rawNewapi.enabled),
      base_url: typeof rawNewapi.base_url === "string" ? rawNewapi.base_url : "",
      api_key: typeof rawNewapi.api_key === "string" ? rawNewapi.api_key : "",
      timeout_seconds: Number(rawNewapi.timeout_seconds || 120),
    },
  };
}

function normalizeFiles(items: CPARemoteFile[]) {
  const seen = new Set<string>();
  const files: CPARemoteFile[] = [];
  for (const item of items) {
    const name = String(item.name || "").trim();
    if (!name || seen.has(name)) {
      continue;
    }
    seen.add(name);
    files.push({
      name,
      email: String(item.email || "").trim(),
      type: typeof item.type === "string" ? item.type : "",
      provider: typeof item.provider === "string" ? item.provider : "",
      status_code: typeof item.status_code === "number" ? item.status_code : null,
      status_message: typeof item.status_message === "string" ? item.status_message : "",
    });
  }
  return files;
}

function updatePoolInList(pools: CPAPool[], poolId: string, updates: Partial<CPAPool>) {
  return pools.map((pool) => (pool.id === poolId ? { ...pool, ...updates } : pool));
}

type SettingsStore = {
  config: SettingsConfig | null;
  isLoadingConfig: boolean;
  isSavingConfig: boolean;

  pools: CPAPool[];
  isLoadingPools: boolean;
  deletingId: string | null;
  loadingFilesId: string | null;
  recoveringId: string | null;

  dialogOpen: boolean;
  editingPool: CPAPool | null;
  formName: string;
  formBaseUrl: string;
  formSecretKey: string;
  showSecret: boolean;
  isSavingPool: boolean;

  browserOpen: boolean;
  browserPool: CPAPool | null;
  remoteFiles: CPARemoteFile[];
  selectedNames: string[];
  fileQuery: string;
  filePage: number;
  pageSize: PageSizeOption;
  isStartingImport: boolean;

  initialize: () => Promise<void>;
  loadConfig: () => Promise<void>;
  saveConfig: () => Promise<void>;
  setAuthKey: (value: string) => void;
  setAdminPassword: (value: string) => void;
  setRefreshAccountIntervalMinute: (value: string) => void;
  setProxy: (value: string) => void;
  setBaseUrl: (value: string) => void;
  setNewAPIEnabled: (value: boolean) => void;
  setNewAPIBaseUrl: (value: string) => void;
  setNewAPIApiKey: (value: string) => void;
  setNewAPITimeoutSeconds: (value: string) => void;

  loadPools: (silent?: boolean) => Promise<void>;
  openAddDialog: () => void;
  openEditDialog: (pool: CPAPool) => void;
  setDialogOpen: (open: boolean) => void;
  setFormName: (value: string) => void;
  setFormBaseUrl: (value: string) => void;
  setFormSecretKey: (value: string) => void;
  setShowSecret: (checked: boolean) => void;
  savePool: () => Promise<void>;
  deletePool: (pool: CPAPool) => Promise<void>;
  startRecover401: (pool: CPAPool) => Promise<void>;

  browseFiles: (pool: CPAPool) => Promise<void>;
  setBrowserOpen: (open: boolean) => void;
  toggleFile: (name: string, checked: boolean) => void;
  replaceSelectedNames: (names: string[]) => void;
  setFileQuery: (value: string) => void;
  setFilePage: (page: number) => void;
  setPageSize: (value: PageSizeOption) => void;
  startImport: () => Promise<void>;
};

export const useSettingsStore = create<SettingsStore>((set, get) => ({
  config: null,
  isLoadingConfig: true,
  isSavingConfig: false,

  pools: [],
  isLoadingPools: true,
  deletingId: null,
  loadingFilesId: null,
  recoveringId: null,

  dialogOpen: false,
  editingPool: null,
  formName: "",
  formBaseUrl: "",
  formSecretKey: "",
  showSecret: false,
  isSavingPool: false,

  browserOpen: false,
  browserPool: null,
  remoteFiles: [],
  selectedNames: [],
  fileQuery: "",
  filePage: 1,
  pageSize: "100",
  isStartingImport: false,

  initialize: async () => {
    await Promise.allSettled([get().loadConfig(), get().loadPools()]);
  },

  loadConfig: async () => {
    set({ isLoadingConfig: true });
    try {
      const data = await fetchSettingsConfig();
      set({ config: normalizeConfig(data.config) });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载系统配置失败");
    } finally {
      set({ isLoadingConfig: false });
    }
  },

  saveConfig: async () => {
    const { config } = get();
    if (!config) {
      return;
    }

    const currentNewapi: NonNullable<SettingsConfig["newapi"]> =
      config.newapi && typeof config.newapi === "object" ? config.newapi : {};
    set({ isSavingConfig: true });
    try {
      const data = await updateSettingsConfig({
        ...config,
        "auth-key": String(config["auth-key"] || "").trim(),
        "admin-password": String(config["admin-password"] || "").trim(),
        refresh_account_interval_minute: Math.max(1, Number(config.refresh_account_interval_minute) || 1),
        proxy: String(config.proxy || "").trim(),
        base_url: String(config.base_url || "").trim(),
        newapi: {
          enabled: Boolean(currentNewapi.enabled),
          base_url: String(currentNewapi.base_url || "").trim(),
          api_key: String(currentNewapi.api_key || "").trim(),
          timeout_seconds: Math.max(5, Number(currentNewapi.timeout_seconds) || 120),
        },
      });
      set({ config: normalizeConfig(data.config) });
      toast.success("配置已保存");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存系统配置失败");
    } finally {
      set({ isSavingConfig: false });
    }
  },

  setAuthKey: (value) => {
    set((state) => ({
      config: state.config ? { ...state.config, "auth-key": value } : null,
    }));
  },

  setAdminPassword: (value) => {
    set((state) => ({
      config: state.config ? { ...state.config, "admin-password": value } : null,
    }));
  },

  setRefreshAccountIntervalMinute: (value) => {
    set((state) => ({
      config: state.config ? { ...state.config, refresh_account_interval_minute: value } : null,
    }));
  },

  setProxy: (value) => {
    set((state) => ({
      config: state.config ? { ...state.config, proxy: value } : null,
    }));
  },

  setBaseUrl: (value) => {
    set((state) => ({
      config: state.config ? { ...state.config, base_url: value } : null,
    }));
  },

  setNewAPIEnabled: (value) => {
    set((state) => ({
      config: state.config
        ? {
            ...state.config,
            newapi: {
              ...(state.config.newapi && typeof state.config.newapi === "object" ? state.config.newapi : {}),
              enabled: value,
            },
          }
        : null,
    }));
  },

  setNewAPIBaseUrl: (value) => {
    set((state) => ({
      config: state.config
        ? {
            ...state.config,
            newapi: {
              ...(state.config.newapi && typeof state.config.newapi === "object" ? state.config.newapi : {}),
              base_url: value,
            },
          }
        : null,
    }));
  },

  setNewAPIApiKey: (value) => {
    set((state) => ({
      config: state.config
        ? {
            ...state.config,
            newapi: {
              ...(state.config.newapi && typeof state.config.newapi === "object" ? state.config.newapi : {}),
              api_key: value,
            },
          }
        : null,
    }));
  },

  setNewAPITimeoutSeconds: (value) => {
    set((state) => ({
      config: state.config
        ? {
            ...state.config,
            newapi: {
              ...(state.config.newapi && typeof state.config.newapi === "object" ? state.config.newapi : {}),
              timeout_seconds: value,
            },
          }
        : null,
    }));
  },

  loadPools: async (silent = false) => {
    if (!silent) {
      set({ isLoadingPools: true });
    }
    try {
      const data = await fetchCPAPools();
      set({ pools: data.pools });
    } catch (error) {
      if (!silent) {
        toast.error(error instanceof Error ? error.message : "加载 CPA 连接失败");
      }
    } finally {
      if (!silent) {
        set({ isLoadingPools: false });
      }
    }
  },

  openAddDialog: () => {
    set({
      editingPool: null,
      formName: "",
      formBaseUrl: "",
      formSecretKey: "",
      showSecret: false,
      dialogOpen: true,
    });
  },

  openEditDialog: (pool) => {
    set({
      editingPool: pool,
      formName: pool.name,
      formBaseUrl: pool.base_url,
      formSecretKey: "",
      showSecret: false,
      dialogOpen: true,
    });
  },

  setDialogOpen: (open) => {
    set({ dialogOpen: open });
  },

  setFormName: (value) => {
    set({ formName: value });
  },

  setFormBaseUrl: (value) => {
    set({ formBaseUrl: value });
  },

  setFormSecretKey: (value) => {
    set({ formSecretKey: value });
  },

  setShowSecret: (checked) => {
    set({ showSecret: checked });
  },

  savePool: async () => {
    const { editingPool, formName, formBaseUrl, formSecretKey } = get();
    if (!formBaseUrl.trim()) {
      toast.error("请输入 CPA 地址");
      return;
    }
    if (!editingPool && !formSecretKey.trim()) {
      toast.error("请输入 Secret Key");
      return;
    }

    set({ isSavingPool: true });
    try {
      if (editingPool) {
        const data = await updateCPAPool(editingPool.id, {
          name: formName.trim(),
          base_url: formBaseUrl.trim(),
          secret_key: formSecretKey.trim() || undefined,
        });
        set({ pools: data.pools, dialogOpen: false });
        toast.success("连接已更新");
      } else {
        const data = await createCPAPool({
          name: formName.trim(),
          base_url: formBaseUrl.trim(),
          secret_key: formSecretKey.trim(),
        });
        set({ pools: data.pools, dialogOpen: false });
        toast.success("连接已添加");
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存失败");
    } finally {
      set({ isSavingPool: false });
    }
  },

  deletePool: async (pool) => {
    set({ deletingId: pool.id });
    try {
      const data = await deleteCPAPool(pool.id);
      set({ pools: data.pools });
      toast.success("连接已删除");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除失败");
    } finally {
      set({ deletingId: null });
    }
  },

  startRecover401: async (pool) => {
    set({ recoveringId: pool.id });
    try {
      const result = await startCPARecover401(pool.id);
      set((state) => ({
        pools: updatePoolInList(state.pools, pool.id, { recover_job: result.recover_job }),
      }));
      if (result.recover_job?.total) {
        toast.success(`401 回收任务已启动，共 ${result.recover_job.total} 个远端账号`);
      } else {
        toast.success("没有检测到可回收的 401 账号");
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "启动 401 回收失败");
    } finally {
      set({ recoveringId: null });
    }
  },

  browseFiles: async (pool) => {
    set({ loadingFilesId: pool.id });
    try {
      const data = await fetchCPAPoolFiles(pool.id);
      const files = normalizeFiles(data.files);
      set({
        browserPool: pool,
        remoteFiles: files,
        selectedNames: [],
        fileQuery: "",
        filePage: 1,
        browserOpen: true,
      });
      toast.success(`读取成功，共 ${files.length} 个远程账号`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "读取远程账号失败");
    } finally {
      set({ loadingFilesId: null });
    }
  },

  setBrowserOpen: (open) => {
    set({ browserOpen: open });
  },

  toggleFile: (name, checked) => {
    set((state) => {
      if (checked) {
        return {
          selectedNames: Array.from(new Set([...state.selectedNames, name])),
        };
      }
      return {
        selectedNames: state.selectedNames.filter((item) => item !== name),
      };
    });
  },

  replaceSelectedNames: (names) => {
    set({ selectedNames: Array.from(new Set(names)) });
  },

  setFileQuery: (value) => {
    set({ fileQuery: value, filePage: 1 });
  },

  setFilePage: (page) => {
    set({ filePage: page });
  },

  setPageSize: (value) => {
    set({ pageSize: value, filePage: 1 });
  },

  startImport: async () => {
    const { browserPool, selectedNames } = get();
    if (!browserPool) {
      return;
    }
    if (selectedNames.length === 0) {
      toast.error("请先选择要导入的账号");
      return;
    }

    set({ isStartingImport: true });
    try {
      const result = await startCPAImport(browserPool.id, selectedNames);
      set((state) => ({
        pools: updatePoolInList(state.pools, browserPool.id, { import_job: result.import_job }),
        browserOpen: false,
      }));
      toast.success("导入任务已启动");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "启动导入失败");
    } finally {
      set({ isStartingImport: false });
    }
  },
}));
