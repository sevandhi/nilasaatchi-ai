import { Route, Routes } from "react-router-dom";
import { NavRail } from "./components/layout/NavRail.jsx";
import { TopBar } from "./components/layout/TopBar.jsx";
import { ReadOnlyBanner } from "./components/common/ReadOnlyBanner.jsx";
import { StatusFooter } from "./components/layout/StatusFooter.jsx";
import { Overview } from "./pages/Overview.jsx";
import { MapWorkspace } from "./pages/MapWorkspace.jsx";
import { ParcelPage } from "./pages/Parcel.jsx";
import { Findings } from "./pages/Findings.jsx";
import { AgentConsole } from "./pages/AgentConsole.jsx";
import { Documents } from "./pages/Documents.jsx";
import { ModelsRouting } from "./pages/ModelsRouting.jsx";
import { ReviewQueue } from "./pages/ReviewQueue.jsx";

export default function App() {
  return (
    <div className="flex h-screen flex-col">
      <ReadOnlyBanner />
      <TopBar />
      <div className="flex min-h-0 flex-1">
        <NavRail />
        <main className="min-w-0 flex-1 overflow-auto p-4">
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/map" element={<MapWorkspace />} />
            <Route path="/parcel" element={<ParcelPage />} />
            <Route path="/parcel/:uid" element={<ParcelPage />} />
            <Route path="/findings" element={<Findings />} />
            <Route path="/agent" element={<AgentConsole />} />
            <Route path="/documents" element={<Documents />} />
            <Route path="/models" element={<ModelsRouting />} />
            <Route path="/review" element={<ReviewQueue />} />
          </Routes>
        </main>
      </div>
      <StatusFooter />
    </div>
  );
}
