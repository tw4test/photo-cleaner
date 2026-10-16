import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { AppShell } from './components/layout/AppShell'
import { Dashboard } from './pages/Dashboard'
import { Library } from './pages/Library'
import { Review } from './pages/Review'
import { Videos } from './pages/Videos'
import { Process } from './pages/Process'
import { History } from './pages/History'
import { Settings } from './pages/Settings'

function App() {
  return (
    <BrowserRouter>
      <AppShell>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/library" element={<Library />} />
          <Route path="/review" element={<Review />} />
          <Route path="/videos" element={<Videos />} />
          <Route path="/process" element={<Process />} />
          <Route path="/history" element={<History />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  )
}

export default App
