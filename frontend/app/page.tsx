"use client";

import { FormEvent, useEffect, useState } from "react";
import { insforge } from "../services/insforge";
import type { ClusterResponse, Diagnosis, HistoryRow, InvestigationResponse } from "../types/investigation";

const stages = ["Checking Pods", "Reading Logs", "Analyzing Events", "Inspecting Deployments", "Checking Networking", "AI Reasoning", "Root Cause Found"];
const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export default function Home() {
  const [user, setUser] = useState<{ email?: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [authError, setAuthError] = useState("");
  const [diagnosis, setDiagnosis] = useState<Diagnosis | null>(null);
  const [history, setHistory] = useState<HistoryRow[]>([]);
  const [stage, setStage] = useState(-1);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [clusters, setClusters] = useState<string[]>([]);
  const [selectedContext, setSelectedContext] = useState("");

  useEffect(() => { void insforge.auth.getCurrentUser().then(({ data }) => { setUser(data?.user ?? null); setLoading(false); }).catch(() => setLoading(false)); }, []);

  useEffect(() => {
    if (!user) return;
    let active = true;
    const loadHistory = async () => {
      const { data } = await insforge.database.from("investigations").select("id, created_at, root_cause, namespace, confidence, status").order("created_at", { ascending: false }).limit(10);
      if (active) setHistory((data ?? []) as HistoryRow[]);
    };
    void loadHistory();
    const channel = `investigations:${user.email ?? "user"}`;
    void insforge.realtime.connect().then(() => insforge.realtime.subscribe(channel));
    const handler = () => void loadHistory();
    insforge.realtime.on("investigation_saved", handler);
    return () => { active = false; insforge.realtime.off("investigation_saved", handler); };
  }, [user]);

  useEffect(() => {
    if (!user) return;
    void fetch(`${apiBase}/clusters`).then((response) => response.json() as Promise<ClusterResponse>).then((result) => {
      if (result.status === "success") { setClusters(result.clusters); setSelectedContext(result.clusters[0] ?? ""); }
      else setError(result.error ?? "Unable to load Kubernetes clusters.");
    }).catch(() => setError("Unable to load Kubernetes clusters. Verify the backend and kubeconfig."));
  }, [user]);

  const signIn = async (event: FormEvent) => {
    event.preventDefault(); setAuthError("");
    const { data, error: signInError } = await insforge.auth.signInWithPassword({ email, password });
    if (signInError) setAuthError(signInError.message); else setUser(data?.user ?? null);
  };

  const investigate = async () => {
    setRunning(true); setError(""); setDiagnosis(null); setStage(0);
    const channel = `investigations:${user?.email ?? "user"}`;
    try {
      await insforge.realtime.connect();
      for (let index = 0; index < stages.length - 1; index += 1) {
        setStage(index);
        await insforge.realtime.publish(channel, "investigation_progress", { stage: stages[index] });
        if (index < stages.length - 2) await new Promise((resolve) => setTimeout(resolve, 250));
      }
      const response = await fetch(`${apiBase}/investigate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ context: selectedContext || null }) });
      if (!response.ok) throw new Error(`Backend returned HTTP ${response.status}`);
      const result = (await response.json()) as InvestigationResponse;
      setDiagnosis(result.diagnosis); setStage(stages.length - 1);
      const row = { root_cause: result.diagnosis.root_cause ?? "No root cause identified", namespace: null, confidence: result.diagnosis.confidence ?? 0, status: result.diagnosis.available ? "completed" : "unavailable" };
      await insforge.database.from("investigations").insert([row]);
      await insforge.realtime.publish(channel, "investigation_saved", row);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Investigation failed"); }
    finally { setRunning(false); }
  };

  if (loading) return <main className="flex min-h-screen items-center justify-center">Loading...</main>;
  if (!user) return <main className="flex min-h-screen items-center justify-center px-6"><form onSubmit={signIn} className="w-full max-w-sm rounded-2xl bg-white p-8 shadow-sm ring-1 ring-slate-200"><p className="text-sm font-semibold uppercase tracking-widest text-blue-600">AI Kubernetes Agent</p><h1 className="mt-2 text-2xl font-bold">Sign in</h1><input className="mt-6 w-full rounded border p-3" type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} required /><input className="mt-3 w-full rounded border p-3" type="password" placeholder="Password" value={password} onChange={(e) => setPassword(e.target.value)} required /><button className="mt-5 w-full rounded bg-blue-600 px-4 py-3 font-medium text-white" type="submit">Sign in</button>{authError && <p className="mt-3 text-sm text-red-600">{authError}</p>}</form></main>;
  return <main className="min-h-screen px-6 py-10"><div className="mx-auto max-w-5xl"><header className="flex items-center justify-between"><div><p className="text-sm font-semibold uppercase tracking-widest text-blue-600">AI Kubernetes Agent</p><h1 className="mt-2 text-3xl font-bold">Cluster Dashboard</h1></div><button onClick={() => { void insforge.auth.signOut(); setUser(null); }} className="rounded border px-4 py-2 text-sm">Sign out</button></header><section className="mt-8 rounded-2xl bg-white p-8 shadow-sm ring-1 ring-slate-200"><div className="flex items-center justify-between"><div><h2 className="text-xl font-semibold">Investigate Cluster</h2><p className="mt-1 text-sm text-slate-600">Collect evidence and generate an SRE diagnosis.</p></div><button onClick={() => void investigate()} disabled={running || !selectedContext} className="rounded-lg bg-blue-600 px-5 py-3 font-medium text-white disabled:opacity-50">{running ? "Investigating..." : "Investigate Cluster"}</button></div><label className="mt-6 block text-sm font-medium text-slate-700">Kubernetes cluster context<select value={selectedContext} onChange={(e) => setSelectedContext(e.target.value)} className="mt-2 w-full rounded border p-3" disabled={running}><option value="">Select a cluster</option>{clusters.map((cluster) => <option key={cluster} value={cluster}>{cluster}</option>)}</select></label>{clusters.length === 0 && <p className="mt-3 text-sm text-amber-700">No kubeconfig contexts are available to the backend.</p>}{stage >= 0 && <div className="mt-8 grid gap-2 sm:grid-cols-2">{stages.map((label, index) => <p key={label} className={`rounded p-3 text-sm ${index <= stage ? "bg-emerald-50 text-emerald-800" : "bg-slate-50 text-slate-500"}`}>{index <= stage ? "✓" : "○"} {label}</p>)}</div>}{error && <p className="mt-4 text-sm text-red-600">{error}</p>}</section>{diagnosis?.available && <section className="mt-6 rounded-2xl bg-white p-8 shadow-sm ring-1 ring-slate-200"><h2 className="text-xl font-semibold">Diagnosis</h2><div className="mt-5 grid gap-5"><div><h3 className="font-medium">Root Cause</h3><p className="mt-1 text-slate-700">{diagnosis.root_cause}</p></div><div><h3 className="font-medium">Explanation</h3><p className="mt-1 text-slate-700">{diagnosis.explanation}</p></div><div><h3 className="font-medium">Suggested Fix</h3><p className="mt-1 text-slate-700">{diagnosis.suggested_fix}</p></div><div><h3 className="font-medium">kubectl Commands</h3><pre className="mt-1 overflow-auto rounded bg-slate-900 p-4 text-sm text-slate-100">{diagnosis.kubectl_commands?.join("\n")}</pre></div><p className="font-semibold text-blue-700">Confidence: {diagnosis.confidence}%</p></div></section>}{diagnosis && !diagnosis.available && <section className="mt-6 rounded-xl bg-amber-50 p-5 text-amber-900">Diagnosis unavailable: {diagnosis.error}</section>}<section className="mt-6 rounded-2xl bg-white p-8 shadow-sm ring-1 ring-slate-200"><h2 className="text-xl font-semibold">Recent Investigations</h2><div className="mt-4 overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr className="border-b text-slate-500"><th className="p-3">Time</th><th className="p-3">Root Cause</th><th className="p-3">Confidence</th><th className="p-3">Status</th></tr></thead><tbody>{history.map((item) => <tr className="border-b" key={item.id}><td className="p-3">{new Date(item.created_at).toLocaleString()}</td><td className="p-3">{item.root_cause}</td><td className="p-3">{item.confidence}%</td><td className="p-3">{item.status}</td></tr>)}</tbody></table>{history.length === 0 && <p className="py-6 text-sm text-slate-500">No investigations yet.</p>}</div></section></div></main>;
}
