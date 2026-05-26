import { Navigate, Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { RecordDetail } from './routes/RecordDetail'
import { Review } from './routes/Review'
import { Uploads } from './routes/Uploads'

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Navigate to="/uploads" replace />} />
        <Route path="/uploads" element={<Uploads />} />
        <Route path="/review" element={<Review />} />
        <Route path="/records/:id" element={<RecordDetail />} />
      </Route>
    </Routes>
  )
}
