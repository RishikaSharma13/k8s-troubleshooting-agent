export default function Home() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <section className="w-full max-w-xl rounded-2xl bg-white p-10 text-center shadow-sm ring-1 ring-slate-200">
        <p className="mb-3 text-sm font-semibold uppercase tracking-widest text-blue-600">AI Kubernetes Agent</p>
        <h1 className="text-3xl font-bold tracking-tight">Troubleshoot Kubernetes with AI</h1>
        <button className="mt-8 rounded-lg bg-blue-600 px-5 py-3 font-medium text-white transition hover:bg-blue-700">Investigate Cluster</button>
        <p className="mt-8 text-sm text-slate-600"><span className="mr-2 inline-block h-2 w-2 rounded-full bg-emerald-500" />System Status: Ready</p>
      </section>
    </main>
  );
}
