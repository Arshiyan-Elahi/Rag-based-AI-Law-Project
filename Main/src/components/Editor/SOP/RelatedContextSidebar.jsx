import React, { useEffect, useState } from 'react';
import { getRelatedContext, deleteLink } from '../../../api/editorApi';
import { Link, AlertTriangle, ShieldCheck, ClipboardCheck, HelpCircle, X } from 'lucide-react';

const RelatedContextSidebar = ({ sopId, onLinkClick }) => {
  const [context, setContext] = useState(null);
  const [loading, setLoading] = useState(false);

  const fetchContext = async () => {
    if (!sopId) return;
    setLoading(true);
    try {
      const data = await getRelatedContext(sopId);
      setContext(data);
    } catch (err) {
      console.error('Failed to load related context:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchContext();
  }, [sopId]);

  const handleDeleteLink = async (linkType, linkId) => {
    if (!window.confirm('Verknüpfung wirklich löschen?')) return;
    try {
      await deleteLink(linkType, linkId);
      fetchContext();
    } catch (err) {
      alert('Fehler beim Löschen der Verknüpfung');
    }
  };

  if (!sopId) return (
    <div className="p-4 text-gray-500 text-sm italic">
      SOP speichern zum Anzeigen von Verknüpfungen.
    </div>
  );

  return (
    <div className="flex flex-col h-full bg-white border-l border-gray-200 w-80 overflow-y-auto">
      <div className="p-4 border-b border-gray-200 bg-gray-50 flex justify-between items-center">
        <h3 className="font-semibold text-gray-800 flex items-center gap-2">
          <Link size={18} className="text-secondary-600" />
          Verknüpfter Kontext
        </h3>
        <button 
          onClick={onLinkClick}
          className="text-xs bg-secondary-600 text-white px-2 py-1 rounded hover:bg-secondary-700 transition"
        >
          + Add Link
        </button>
      </div>

      <div className="p-4 space-y-6">
        {/* Deviations */}
        <ContextSection 
          title="Abweichungen" 
          items={context?.related_deviations || []} 
          icon={<AlertTriangle size={16} className="text-amber-500" />}
          emptyText="Keine Abweichungen verknüpft"
        />

        {/* CAPAs */}
        <ContextSection 
          title="CAPA Maßnahmen" 
          items={context?.related_capas || []} 
          icon={<ShieldCheck size={16} className="text-emerald-500" />}
          emptyText="Keine CAPAs verknüpft"
        />

        {/* Audits */}
        <ContextSection 
          title="Audit Findings" 
          items={context?.related_audit_findings || []} 
          icon={<ClipboardCheck size={16} className="text-blue-500" />}
          emptyText="Keine Findings verknüpft"
        />

        {/* Decisions */}
        <ContextSection 
          title="Entscheidungen" 
          items={context?.related_decisions || []} 
          icon={<HelpCircle size={16} className="text-purple-500" />}
          emptyText="Keine Entscheidungen verknüpft"
        />
      </div>
    </div>
  );
};

const ContextSection = ({ title, items, icon, emptyText }) => (
  <div className="space-y-2">
    <h4 className="text-xs font-bold text-gray-400 uppercase tracking-wider flex items-center gap-2">
      {icon}
      {title}
    </h4>
    {items.length === 0 ? (
      <p className="text-xs text-gray-400 italic pl-6">{emptyText}</p>
    ) : (
      <div className="space-y-2 pl-6">
        {items.map(item => (
          <div key={item.id} className="group relative bg-gray-50 border border-gray-100 p-2 rounded text-xs hover:border-secondary-200 transition">
            <div className="font-medium text-gray-700 truncate pr-4">{item.title || item.deviation_number || item.capa_number || item.finding_number}</div>
            <div className="text-[10px] text-gray-500 mt-1">{item.external_status || 'Offen'}</div>
          </div>
        ))}
      </div>
    )}
  </div>
);

export default RelatedContextSidebar;
