import React, { useState, useEffect, useCallback } from 'react';
import { Search, Plus, Filter, Download, Loader, AlertTriangle, ShieldCheck, ClipboardCheck, HelpCircle } from 'lucide-react';
import { getDeviations, getCAPAs, getAuditFindings, getDecisions } from '../api/editorApi';

const typeConfig = {
  deviations: {
    title: 'Abweichungen (Deviations)',
    fetch: getDeviations,
    icon: <AlertTriangle className="text-amber-500" />,
    color: 'amber',
    codeKey: 'deviation_number'
  },
  capas: {
    title: 'CAPA Maßnahmen',
    fetch: getCAPAs,
    icon: <ShieldCheck className="text-emerald-500" />,
    color: 'emerald',
    codeKey: 'capa_number'
  },
  audits: {
    title: 'Audit Findings',
    fetch: getAuditFindings,
    icon: <ClipboardCheck className="text-blue-500" />,
    color: 'blue',
    codeKey: 'finding_number'
  },
  decisions: {
    title: 'Entscheidungen (Decisions)',
    fetch: getDecisions,
    icon: <HelpCircle className="text-purple-500" />,
    color: 'purple',
    codeKey: 'decision_number'
  }
};

export default function EntitiesPage({ type }) {
  const config = typeConfig[type];
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [searchTerm, setSearchTerm] = useState('');

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await config.fetch();
      setItems(data || []);
    } catch (err) {
      setError(`Fehler beim Laden von ${config.title}`);
    } finally {
      setLoading(false);
    }
  }, [config]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const filteredItems = items.filter(item => 
    (item.title || '').toLowerCase().includes(searchTerm.toLowerCase()) ||
    (item[config.codeKey] || '').toLowerCase().includes(searchTerm.toLowerCase())
  );

  return (
    <div className="p-8 max-w-7xl mx-auto space-y-6">
      <header className="flex justify-between items-center">
        <div className="flex items-center gap-3">
          {config.icon}
          <h1 className="text-2xl font-bold text-gray-800">{config.title}</h1>
        </div>
        <div className="flex gap-2">
           <button className="flex items-center gap-2 px-4 py-2 border border-gray-200 rounded-lg hover:bg-gray-50 transition text-sm font-medium">
             <Download size={16} /> Export
           </button>
           <button className={`flex items-center gap-2 px-4 py-2 bg-secondary-600 text-white rounded-lg hover:bg-secondary-700 transition text-sm font-medium`}>
             <Plus size={16} /> Neu hinzufügen
           </button>
        </div>
      </header>

      <section className="bg-white p-4 rounded-xl shadow-sm border border-gray-100 flex gap-4 items-center">
        <div className="flex-1 relative">
          <Search className="absolute left-3 top-2.5 text-gray-400" size={18} />
          <input 
            type="text" 
            placeholder="Suchen..."
            className="w-full pl-10 pr-4 py-2 bg-gray-50 border border-gray-200 rounded-lg focus:ring-2 focus:ring-secondary-500 focus:border-transparent outline-none"
            value={searchTerm}
            onChange={e => setSearchTerm(e.target.value)}
          />
        </div>
        <button className="flex items-center gap-2 px-4 py-2 border border-gray-200 rounded-lg text-sm font-medium hover:bg-gray-50">
          <Filter size={16} /> Filter
        </button>
      </section>

      <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
        <table className="w-full text-left text-sm">
          <thead className="bg-gray-50 border-b border-gray-100 uppercase text-gray-500 font-bold text-[10px] tracking-wider">
            <tr>
              <th className="px-6 py-4">Nummer</th>
              <th className="px-6 py-4">Titel / Beschreibung</th>
              <th className="px-6 py-4">Status</th>
              <th className="px-6 py-4">Erstellt am</th>
              <th className="px-6 py-4 text-right">Aktionen</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading ? (
              <tr>
                <td colSpan="5" className="px-6 py-12 text-center text-gray-400">
                  <Loader className="animate-spin mx-auto mb-2" /> Lade Daten...
                </td>
              </tr>
            ) : filteredItems.length === 0 ? (
              <tr>
                <td colSpan="5" className="px-6 py-12 text-center text-gray-400">Keine Einträge gefunden.</td>
              </tr>
            ) : filteredItems.map(item => (
              <tr key={item.id} className="hover:bg-gray-50 transition cursor-pointer">
                <td className="px-6 py-4 font-bold text-secondary-600">{item[config.codeKey] || '—'}</td>
                <td className="px-6 py-4 font-medium text-gray-800">{item.title || item.description_text?.slice(0, 50) || 'Unbenannt'}</td>
                <td className="px-6 py-4">
                  <span className="px-2 py-1 rounded-full bg-amber-50 text-amber-600 text-[10px] font-bold uppercase">{item.external_status || item.acceptance_status || 'Offen'}</span>
                </td>
                <td className="px-6 py-4 text-gray-500">{new Date(item.created_at).toLocaleDateString('de-DE')}</td>
                <td className="px-6 py-4 text-right text-secondary-600 font-bold hover:underline">Details</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
