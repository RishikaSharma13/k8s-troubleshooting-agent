"use client";

import { FormEvent, useEffect, useState } from "react";
import { insforge } from "../services/insforge";
import type { Cluster, ClusterResponse, Diagnosis, HistoryRow, InvestigationResponse } from "../types/investigation";

const stages = ["Checking Pods", "Reading Logs", "Analyzing Events", "Inspecting Deployments", "Checking Networking", "AI Reasoning", "Root Cause Found"];
const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export default function Home() {
  const [user, setUser] = useState<{ email?: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [verificationCode, setVerificationCode] = useState("");
  const [authMode, setAuthMode] = useState<"sign-in" | "sign-up" | "verify">("sign-in");
  const [authMessage, setAuthMessage] = useState("");
  const [authError, setAuthError] = useState("");
  const [diagnosis, setDiagnosis] = useState<Diagnosis | null>(null);
  const [history, setHistory] = useState<HistoryRow[]>([]);
  const [stage, setStage] = useState(-1);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [clusters, setClusters] = useState<Cluster[]>([]);
  const [selectedContext, setSelectedContext] = useState("");
  const [clustersLoading, setClustersLoading] = useState(false);

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

  const loadClusters = async () => {
    setClustersLoading(true);
    try {
      const result = await fetch(`${apiBase}/clusters`).then((response) => response.json() as Promise<ClusterResponse>);
      if (result.status === "success") {
        setClusters(result.clusters);
        setSelectedContext((current) => result.clusters.some((cluster) => cluster.context === current) ? current : result.clusters.find((cluster) => cluster.current)?.context ?? result.clusters[0]?.context ?? "");
      } else setError(result.error ?? "Unable to load Kubernetes clusters.");
    } catch { setError("Unable to load Kubernetes clusters. Verify the backend and kubeconfig."); }
    finally { setClustersLoading(false); }
  };

  useEffect(() => { if (user) void loadClusters(); }, [user]);

  const signIn = async (event: FormEvent) => {
    event.preventDefault(); setAuthError(""); setAuthMessage("");
    const { data, error: signInError } = await insforge.auth.signInWithPassword({ email, password });
    if (signInError) setAuthError(signInError.message); else setUser(data?.user ?? null);
  };

  const signUp = async (event: FormEvent) => {
    event.preventDefault(); setAuthError(""); setAuthMessage("");
    const { error: signUpError } = await insforge.auth.signUp({ email, password, name: name || undefined });
    if (signUpError) {
      setAuthError(signUpError.message);
      return;
    }
    setAuthMode("verify");
    setAuthMessage("We sent a 6-digit verification code to your email.");
  };

  const verifyEmail = async (event: FormEvent) => {
    event.preventDefault(); setAuthError(""); setAuthMessage("");
    const { error: verifyError } = await insforge.auth.verifyEmail({ email, otp: verificationCode });
    if (verifyError) {
      setAuthError(verifyError.message);
      return;
    }
    setAuthMode("sign-in");
    setAuthMessage("Email verified. You can now sign in.");
    setVerificationCode("");
  };

  const resendVerification = async () => {
    setAuthError(""); setAuthMessage("");
    const { error: resendError } = await insforge.auth.resendVerificationEmail({ email });
    if (resendError) setAuthError(resendError.message);
    else setAuthMessage("A new verification code was sent.");
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
  if (!user) return <main className="flex min-h-screen items-center justify-center px-6"><form onSubmit={authMode === "sign-in" ? signIn : authMode === "sign-up" ? signUp : verifyEmail} className="w-full max-w-sm rounded-2xl bg-white p-8 shadow-sm ring-1 ring-slate-200"><p className="text-sm font-semibold uppercase tracking-widest text-blue-600">AI Kubernetes Agent</p><h1 className="mt-2 text-2xl font-bold">{authMode === "sign-in" ? "Sign in" : authMode === "sign-up" ? "Create account" : "Verify email"}</h1>{authMode === "verify" ? <><p className="mt-2 text-sm text-slate-600">Enter the 6-digit code sent to {email}.</p><input className="mt-6 w-full rounded border p-3" type="text" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} placeholder="6-digit code" value={verificationCode} onChange={(e) => setVerificationCode(e.target.value)} required /><button className="mt-5 w-full rounded bg-blue-600 px-4 py-3 font-medium text-white" type="submit">Verify email</button><button className="mt-3 w-full text-sm text-blue-600" type="button" onClick={() => void resendVerification()}>Resend code</button></> : <><>{authMode === "sign-up" && <input className="mt-6 w-full rounded border p-3" type="text" placeholder="Name (optional)" value={name} onChange={(e) => setName(e.target.value)} />}</><input className={`${authMode === "sign-up" ? "mt-3" : "mt-6"} w-full rounded border p-3`} type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} required /><input className="mt-3 w-full rounded border p-3" type="password" placeholder="Password" value={password} onChange={(e) => setPassword(e.target.value)} required /><button className="mt-5 w-full rounded bg-blue-600 px-4 py-3 font-medium text-white" type="submit">{authMode === "sign-in" ? "Sign in" : "Sign up"}</button></>}{authMessage && <p className="mt-3 text-sm text-emerald-700">{authMessage}</p>}{authError && <p className="mt-3 text-sm text-red-600">{authError}</p>}<p className="mt-5 text-center text-sm text-slate-600">{authMode === "sign-in" ? <>Need an account? <button type="button" className="text-blue-600" onClick={() => { setAuthMode("sign-up"); setAuthError(""); setAuthMessage(""); }}>Sign up</button></> : authMode === "sign-up" ? <>Already have an account? <button type="button" className="text-blue-600" onClick={() => { setAuthMode("sign-in"); setAuthError(""); setAuthMessage(""); }}>Sign in</button></> : <button type="button" className="text-blue-600" onClick={() => { setAuthMode("sign-in"); setAuthError(""); setAuthMessage(""); }}>Back to sign in</button>}</p></form></main>;
  return <main className="min-h-screen px-6 py-10"><div className="mx-auto max-w-5xl"><header className="flex items-center justify-between"><div><p className="text-sm font-semibold uppercase tracking-widest text-blue-600">AI Kubernetes Agent</p><h1 className="mt-2 text-3xl font-bold">Cluster Dashboard</h1></div><button onClick={() => { void insforge.auth.signOut(); setUser(null); }} className="rounded border px-4 py-2 text-sm">Sign out</button></header><section className="mt-8 rounded-2xl bg-white p-8 shadow-sm ring-1 ring-slate-200"><div className="flex flex-wrap items-center justify-between gap-4"><div><h2 className="text-xl font-semibold">Investigate Cluster</h2><p className="mt-1 text-sm text-slate-600">Collect evidence and generate an SRE diagnosis.</p></div><div className="flex items-center gap-3"><button onClick={() => void investigate()} disabled={running || !selectedContext} className="rounded-lg bg-blue-600 px-5 py-3 font-medium text-white disabled:opacity-50">{running ? "Investigating..." : "Investigate Cluster"}</button><button onClick={() => void loadClusters()} disabled={clustersLoading || running} className="rounded-lg border px-4 py-3 text-sm">{clustersLoading ? "Refreshing..." : "Refresh clusters"}</button></div></div><div className="mt-8 flex items-center justify-between"><div><h3 className="font-medium">Select cluster</h3><p className="mt-1 text-sm text-slate-600">{clusters.length} cluster{clusters.length === 1 ? "" : "s"} available</p></div></div>{clusters.length > 0 ? <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{clusters.map((cluster) => { const selected = selectedContext === cluster.context; return <button key={cluster.context} type="button" onClick={() => setSelectedContext(cluster.context)} disabled={running} className={`min-h-40 rounded-xl border p-5 text-left transition ${selected ? "border-cyan-400 bg-cyan-950/50 shadow-lg shadow-cyan-950/40" : "border-slate-700 bg-slate-950/30 hover:border-cyan-700"}`}><div className="flex items-start justify-between gap-2"><span className={`flex h-9 w-9 items-center justify-center rounded-lg ${selected ? "bg-cyan-500/20 text-cyan-300" : "bg-slate-800 text-slate-400"}`}>◇</span><span className="flex gap-1 text-[10px] font-semibold uppercase tracking-wider">{cluster.current && <span className="rounded-full bg-emerald-500/20 px-2 py-1 text-emerald-300">Current</span>}{selected && <span className="rounded-full bg-cyan-500/20 px-2 py-1 text-cyan-300">Selected</span>}</span></div><p className="mt-5 truncate font-semibold text-slate-100">{cluster.name}</p><p className="mt-1 truncate text-xs text-slate-400">{cluster.cluster || cluster.context}</p><p className="mt-4 truncate text-xs text-slate-500">{cluster.server || "API server unavailable"}</p></button>; })}</div> : <p className="mt-4 rounded-lg bg-slate-950/40 p-5 text-sm text-amber-300">No kubeconfig contexts are available to the backend.</p>}{running && <div className="investigation-banner mt-8"><span className="investigation-dot" />Investigating Kubernetes Cluster...<span className="investigation-dots">•••</span></div>}{stage >= 0 && <div className="investigation-timeline mt-8"><h3 className="mb-4 text-sm font-semibold uppercase tracking-widest text-slate-400">Investigation status</h3>{stages.map((label, index) => { const complete = index < stage || (!running && index <= stage); const active = running && index === stage; return <div key={label} className={`timeline-item ${complete ? "complete" : active ? "active" : "pending"}`}><span className="timeline-icon">{complete ? "✓" : active ? "" : "○"}</span><span>{label}</span></div>; })}</div>}{error && <p className="mt-4 text-sm text-red-600">{error}</p>}</section>{diagnosis?.available && <section className="mt-6 rounded-2xl bg-white p-8 shadow-sm ring-1 ring-slate-200"><h2 className="text-xl font-semibold">Diagnosis</h2><div className="mt-5 grid gap-5"><div><h3 className="font-medium">Root Cause</h3><p className="mt-1 text-slate-700">{diagnosis.root_cause}</p></div><div><h3 className="font-medium">Explanation</h3><p className="mt-1 text-slate-700">{diagnosis.explanation}</p></div><div><h3 className="font-medium">Suggested Fix</h3><p className="mt-1 text-slate-700">{diagnosis.suggested_fix}</p></div><div><h3 className="font-medium">kubectl Commands</h3><pre className="mt-1 overflow-auto rounded bg-slate-900 p-4 text-sm text-slate-100">{diagnosis.kubectl_commands?.join("\n")}</pre></div><p className="font-semibold text-blue-700">Confidence: {diagnosis.confidence}%</p></div></section>}{diagnosis && !diagnosis.available && <section className="mt-6 rounded-xl bg-amber-50 p-5 text-amber-900">Diagnosis unavailable: {diagnosis.error}</section>}<section className="mt-6 rounded-2xl bg-white p-8 shadow-sm ring-1 ring-slate-200"><h2 className="text-xl font-semibold">Recent Investigations</h2><div className="mt-4 overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr className="border-b text-slate-500"><th className="p-3">Time</th><th className="p-3">Root Cause</th><th className="p-3">Confidence</th><th className="p-3">Status</th></tr></thead><tbody>{history.map((item) => <tr className="border-b" key={item.id}><td className="p-3">{new Date(item.created_at).toLocaleString()}</td><td className="p-3">{item.root_cause}</td><td className="p-3">{item.confidence}%</td><td className="p-3">{item.status}</td></tr>)}</tbody></table>{history.length === 0 && <p className="py-6 text-sm text-slate-500">No investigations yet.</p>}</div></section></div></main>;
}
