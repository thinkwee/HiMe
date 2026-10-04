/**
 * StatisticsPanel helpers: feature categorisation and the memoisable data pass.
 */
import { render, screen } from '@testing-library/react'
import StatisticsPanel, { computePanelData, getCategory } from '../components/StatisticsPanel'
import { api } from '../test/mocks/api'

vi.mock('../lib/api', () => import('../test/mocks/api'))

// Every feature name the backend can emit (backend/data_readers/apple_health_features.py).
const BACKEND_FEATURES = {
  heart: ['heart_rate', 'resting_heart_rate', 'walking_heart_rate_avg', 'heart_rate_variability',
    'heart_rate_recovery', 'blood_oxygen', 'vo2max', 'respiratory_rate', 'high_heart_rate_event',
    'low_cardio_fitness_event'],
  sleep: ['sleep', 'sleep_in_bed', 'sleep_asleep', 'sleep_awake', 'sleep_core', 'sleep_deep',
    'sleep_rem', 'sleep_efficiency', 'mindful_session', 'sleeping_wrist_temp'],
  activity: ['steps', 'distance', 'distance_cycling', 'flights_climbed', 'exercise_time',
    'stand_time', 'active_energy', 'resting_energy'],
  mobility: ['walking_speed', 'walking_asymmetry', 'walking_double_support', 'walking_steadiness',
    'stair_descent_speed', 'stair_ascent_speed', 'running_ground_contact', 'running_vertical_oscillation'],
}

describe('getCategory', () => {
  it('puts every workout_* feature in Workouts, not Activity (Energy/Cycling/Distance keywords)', () => {
    for (const f of [
      'workout_running_energy', 'workout_cycling_distance', 'workout_swimming_duration',
      'workout_walking_energy', 'workout_hiit_energy', 'workout_cooldown_duration',
      'Workout Running Duration', 'WorkoutCyclingEnergy',
    ]) {
      expect(getCategory(f).id, f).toBe('workouts')
    }
  })

  it('matches CamelCase keywords against snake_case / spaced feature names', () => {
    expect(getCategory('blood_pressure_systolic').id).toBe('heart') // keyword "BloodPressure"
    expect(getCategory('Blood Pressure').id).toBe('heart')
    expect(getCategory('deep_sleep_minutes').id).toBe('sleep') // keyword "DeepSleep"
    expect(getCategory('HKQuantityTypeIdentifierHeartRate').id).toBe('heart')
  })

  it('categorises the real backend feature names sensibly', () => {
    for (const [id, features] of Object.entries(BACKEND_FEATURES)) {
      for (const f of features) {
        expect(getCategory(f).id, f).toBe(id)
      }
    }
  })

  it('falls back to the last (general) category for empty / unknown names', () => {
    expect(getCategory('').id).toBe('body')
    expect(getCategory('zzz_unknown').id).toBe('body')
  })
})

describe('computePanelData', () => {
  const mk = (feature, minute, value, pid = 'LiveUser') => ({
    feature_type: feature,
    date: `2026-03-20T10:${String(minute).padStart(2, '0')}:00`,
    value,
    pid,
  })

  it('groups chart records by feature in one pass', () => {
    const rows = [mk('heart_rate', 1, 60), mk('steps', 2, 10), mk('heart_rate', 3, 62)]
    const out = computePanelData({ data: rows }, rows, '1day', 'individual')
    expect(out.featuresToDisplay.sort()).toEqual(['heart_rate', 'steps'])
    expect(out.featureDataMap.get('heart_rate')).toHaveLength(2)
    expect(out.featureDataMap.get('steps')).toHaveLength(1)
    expect(out.chartData).toHaveLength(3)
  })

  it('does not throw on a very large history (no Math.max(...arr) spread)', () => {
    const rows = []
    for (let i = 0; i < 150000; i++) rows.push(mk('heart_rate', i % 60, 60))
    expect(() => computePanelData({ data: rows.slice(0, 5) }, rows, '1month', 'individual')).not.toThrow()
  })

  it('returns empty structures without data', () => {
    const out = computePanelData(null, [], '1hour', 'individual')
    expect(out.chartData).toEqual([])
    expect(out.featuresToDisplay).toEqual([])
    expect(out.totalMinTs).toBeNull()
  })
})

describe('StatisticsPanel render', () => {
  beforeEach(() => {
    api.getDataCount.mockResolvedValue({ success: true, count: 2 })
  })

  it('renders the category sections for streamed data', () => {
    const rows = [
      { feature_type: 'heart_rate', date: '2026-03-20T10:00:00', value: 60, pid: 'LiveUser' },
      { feature_type: 'workout_running_energy', date: '2026-03-20T10:01:00', value: 5, pid: 'LiveUser' },
    ]
    render(<StatisticsPanel data={{ data: rows }} historicalData={rows} liveHistoryWindow="1day" />)
    expect(screen.getByText('Heart & Vitals')).toBeInTheDocument()
    expect(screen.getByText('Workouts')).toBeInTheDocument()
    expect(screen.queryByText('Activity & Fitness')).not.toBeInTheDocument()
  })
})
