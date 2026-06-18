import React, { useState, useEffect } from 'react';
import { ChevronDown, Lock, AlertCircle, CheckCircle, Copy, Plus, Trash2, Eye, EyeOff } from 'lucide-react';
import './ApiKeyManagement.css';

interface PlatformSettings {
  provider: 'claude';
  keyFingerprint: string;
  lastUpdated: string;
  status: 'valid' | 'invalid' | 'unconfigured';
  tokenUsageMonth: number;
  costMonth: number;
}

interface OrgApiConfig {
  orgId: number;
  orgName: string;
  hasBYOFeature: boolean;
  hasCustomKey: boolean;
  keyFingerprint: string | null;
  lastUpdated: string | null;
  monthlyTokens: number;
  monthlyCost: number;
  estimatedTokens: number;
}

interface FunctionOverride {
  functionId: string;
  functionName: string;
  provider: 'platform' | 'custom';
  keyFingerprint: string | null;
  status: 'active' | 'inactive';
}

const ApiKeyManagement: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'platform' | 'orgs' | 'functions'>('platform');
  const [platformSettings, setPlatformSettings] = useState<PlatformSettings>({
    provider: 'claude',
    keyFingerprint: 'sk-proj-••••••••••••••',
    lastUpdated: new Date().toISOString(),
    status: 'valid',
    tokenUsageMonth: 2845000,
    costMonth: 142.25,
  });

  const [orgs, setOrgs] = useState<OrgApiConfig[]>([
    {
      orgId: 1,
      orgName: 'Acme Corp',
      hasBYOFeature: true,
      hasCustomKey: true,
      keyFingerprint: 'sk-proj-•••••••••••••cde',
      lastUpdated: '2025-05-02',
      monthlyTokens: 450000,
      monthlyCost: 22.5,
      estimatedTokens: 425000,
    },
    {
      orgId: 2,
      orgName: 'TechStart Inc',
      hasBYOFeature: false,
      hasCustomKey: false,
      keyFingerprint: null,
      lastUpdated: null,
      monthlyTokens: 820000,
      monthlyCost: 0,
      estimatedTokens: 780000,
    },
  ]);

  const [functionOverrides, setFunctionOverrides] = useState<FunctionOverride[]>([
    { functionId: 'claude_classify', functionName: 'Company Classification', provider: 'platform', keyFingerprint: null, status: 'active' },
    { functionId: 'noga', functionName: 'NOGA Clustering', provider: 'platform', keyFingerprint: null, status: 'active' },
    { functionId: 'stopword_discovery', functionName: 'Stopword Discovery', provider: 'platform', keyFingerprint: null, status: 'inactive' },
  ]);

  const [showKeyInput, setShowKeyInput] = useState(false);
  const [newKeyInput, setNewKeyInput] = useState('');
  const [expandedOrgId, setExpandedOrgId] = useState<number | null>(null);

  const maskKey = (key: string) => {
    if (!key) return '••••••••••••••••';
    return `${key.slice(0, 8)}...${key.slice(-4)}`;
  };

  const handleCopyFingerprint = (fingerprint: string) => {
    navigator.clipboard.writeText(fingerprint);
  };

  return (
    <div className="api-key-management">
      {/* Header */}
      <div className="akm-header">
        <div className="akm-header-content">
          <h1 className="akm-title">API Key Control Center</h1>
          <p className="akm-subtitle">Manage Claude API keys across platform and organizations</p>
        </div>
        <div className="akm-header-stats">
          <div className="akm-stat">
            <span className="akm-stat-label">Monthly Spend</span>
            <span className="akm-stat-value">${(platformSettings.costMonth + orgs.reduce((sum, org) => sum + org.monthlyCost, 0)).toFixed(2)}</span>
          </div>
          <div className="akm-stat">
            <span className="akm-stat-label">Total Tokens</span>
            <span className="akm-stat-value">{((platformSettings.tokenUsageMonth + orgs.reduce((sum, org) => sum + org.monthlyTokens, 0)) / 1000000).toFixed(1)}M</span>
          </div>
        </div>
      </div>

      {/* Tab Navigation */}
      <div className="akm-tabs">
        <button className={`akm-tab ${activeTab === 'platform' ? 'active' : ''}`} onClick={() => setActiveTab('platform')}>
          <span className="akm-tab-icon">⚙️</span> Platform Settings
        </button>
        <button className={`akm-tab ${activeTab === 'orgs' ? 'active' : ''}`} onClick={() => setActiveTab('orgs')}>
          <span className="akm-tab-icon">🏢</span> Organizations
        </button>
        <button className={`akm-tab ${activeTab === 'functions' ? 'active' : ''}`} onClick={() => setActiveTab('functions')}>
          <span className="akm-tab-icon">⚡</span> Function Overrides
        </button>
      </div>

      {/* Content */}
      <div className="akm-content">
        {/* Platform Settings Tab */}
        {activeTab === 'platform' && (
          <div className="akm-section">
            <div className="akm-card akm-card-primary">
              <div className="akm-card-header">
                <div>
                  <h2>Primary API Key</h2>
                  <p className="akm-card-subtitle">Used by all organizations without bring-your-own-key</p>
                </div>
                <div className={`akm-status-badge ${platformSettings.status}`}>
                  {platformSettings.status === 'valid' && <CheckCircle size={16} />}
                  {platformSettings.status === 'invalid' && <AlertCircle size={16} />}
                  {platformSettings.status === 'unconfigured' && <Lock size={16} />}
                  <span>{platformSettings.status}</span>
                </div>
              </div>

              <div className="akm-card-body">
                <div className="akm-key-display">
                  <div className="akm-key-info">
                    <label className="akm-label">Key Fingerprint</label>
                    <div className="akm-key-value">
                      <code>{platformSettings.keyFingerprint}</code>
                      <button
                        className="akm-copy-btn"
                        onClick={() => handleCopyFingerprint(platformSettings.keyFingerprint)}
                        title="Copy fingerprint"
                      >
                        <Copy size={14} />
                      </button>
                    </div>
                  </div>
                  <div className="akm-key-meta">
                    <div>
                      <label className="akm-label">Last Updated</label>
                      <p className="akm-value">{new Date(platformSettings.lastUpdated).toLocaleDateString()}</p>
                    </div>
                  </div>
                </div>

                <div className="akm-divider" />

                <div className="akm-metrics-grid">
                  <div className="akm-metric">
                    <span className="akm-metric-label">Month-to-Date Tokens</span>
                    <span className="akm-metric-value">{(platformSettings.tokenUsageMonth / 1000000).toFixed(2)}M</span>
                  </div>
                  <div className="akm-metric">
                    <span className="akm-metric-label">Estimated Monthly Cost</span>
                    <span className="akm-metric-value accent-orange">${platformSettings.costMonth.toFixed(2)}</span>
                  </div>
                </div>
              </div>

              <div className="akm-card-footer">
                <button className="akm-btn akm-btn-secondary">Edit Key</button>
                <button className="akm-btn akm-btn-secondary">View Logs</button>
              </div>
            </div>

            {/* Key Configuration */}
            <div className="akm-card">
              <div className="akm-card-header">
                <h2>Update API Key</h2>
              </div>
              <div className="akm-card-body">
                <div className="akm-form-group">
                  <label htmlFor="new-key" className="akm-label">
                    New API Key <span className="akm-badge-small">sk-proj-*</span>
                  </label>
                  <div className="akm-input-group">
                    <input
                      id="new-key"
                      type={showKeyInput ? 'text' : 'password'}
                      value={newKeyInput}
                      onChange={(e) => setNewKeyInput(e.target.value)}
                      placeholder="sk-proj-..."
                      className="akm-input"
                    />
                    <button
                      className="akm-input-action"
                      onClick={() => setShowKeyInput(!showKeyInput)}
                      title={showKeyInput ? 'Hide' : 'Show'}
                    >
                      {showKeyInput ? <EyeOff size={18} /> : <Eye size={18} />}
                    </button>
                  </div>
                  <p className="akm-help-text">Key will be encrypted and never logged</p>
                </div>
              </div>
              <div className="akm-card-footer">
                <button className="akm-btn akm-btn-danger">Update Key</button>
              </div>
            </div>
          </div>
        )}

        {/* Organizations Tab */}
        {activeTab === 'orgs' && (
          <div className="akm-section">
            <div className="akm-org-table-wrapper">
              <table className="akm-org-table">
                <thead>
                  <tr>
                    <th>Organization</th>
                    <th>BYO Feature</th>
                    <th>Key Status</th>
                    <th>Monthly Tokens</th>
                    <th>Monthly Cost</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {orgs.map((org) => (
                    <React.Fragment key={org.orgId}>
                      <tr className={`akm-org-row ${expandedOrgId === org.orgId ? 'expanded' : ''}`}>
                        <td className="akm-org-name">
                          <button
                            className="akm-expand-btn"
                            onClick={() => setExpandedOrgId(expandedOrgId === org.orgId ? null : org.orgId)}
                          >
                            <ChevronDown size={16} />
                          </button>
                          <span>{org.orgName}</span>
                        </td>
                        <td>
                          <span className={`akm-badge ${org.hasBYOFeature ? 'akm-badge-success' : 'akm-badge-default'}`}>
                            {org.hasBYOFeature ? 'Enabled' : 'Disabled'}
                          </span>
                        </td>
                        <td>
                          {org.hasCustomKey ? (
                            <span className="akm-badge akm-badge-success">
                              <CheckCircle size={12} /> Custom
                            </span>
                          ) : (
                            <span className="akm-badge akm-badge-default">Platform</span>
                          )}
                        </td>
                        <td className="akm-mono">{(org.monthlyTokens / 1000000).toFixed(2)}M</td>
                        <td className="akm-mono">${org.monthlyCost.toFixed(2)}</td>
                        <td>
                          <button className="akm-icon-btn" title="Edit">
                            ✏️
                          </button>
                          <button className="akm-icon-btn" title="View Details">
                            👁️
                          </button>
                        </td>
                      </tr>
                      {expandedOrgId === org.orgId && (
                        <tr className="akm-detail-row">
                          <td colSpan={6}>
                            <div className="akm-org-details">
                              <div className="akm-detail-group">
                                <label className="akm-label">Key Fingerprint</label>
                                {org.keyFingerprint ? (
                                  <div className="akm-detail-value">
                                    <code>{org.keyFingerprint}</code>
                                    <button
                                      className="akm-copy-btn"
                                      onClick={() => handleCopyFingerprint(org.keyFingerprint!)}
                                    >
                                      <Copy size={14} />
                                    </button>
                                  </div>
                                ) : (
                                  <p className="akm-no-value">Using platform key</p>
                                )}
                              </div>
                              <div className="akm-detail-group">
                                <label className="akm-label">Last Updated</label>
                                <p className="akm-detail-value">{org.lastUpdated || '—'}</p>
                              </div>
                              <div className="akm-detail-group">
                                <label className="akm-label">Feature Entitlements</label>
                                <div className="akm-feature-list">
                                  {org.hasBYOFeature && <span className="akm-feature-tag">bring_your_own_llm_keys</span>}
                                  {!org.hasBYOFeature && <span className="akm-feature-tag-disabled">No custom keys allowed</span>}
                                </div>
                              </div>
                              {org.hasCustomKey && (
                                <div className="akm-action-group">
                                  <button className="akm-btn akm-btn-secondary akm-btn-sm">Rotate Key</button>
                                  <button className="akm-btn akm-btn-secondary akm-btn-sm">Reset to Platform</button>
                                </div>
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Org Configuration Card */}
            <div className="akm-card" style={{ marginTop: '2rem' }}>
              <div className="akm-card-header">
                <h2>Add Organization BYO Key</h2>
                <p className="akm-card-subtitle">Enable custom API key for an organization</p>
              </div>
              <div className="akm-card-body">
                <div className="akm-form-row">
                  <div className="akm-form-group">
                    <label className="akm-label">Organization</label>
                    <select className="akm-input">
                      <option>Select organization...</option>
                      <option>New Organization</option>
                    </select>
                  </div>
                  <div className="akm-form-group">
                    <label className="akm-label">API Key</label>
                    <input type="password" className="akm-input" placeholder="sk-proj-..." />
                  </div>
                </div>
              </div>
              <div className="akm-card-footer">
                <button className="akm-btn akm-btn-primary">
                  <Plus size={16} /> Add Key
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Function Overrides Tab */}
        {activeTab === 'functions' && (
          <div className="akm-section">
            <div className="akm-info-banner">
              <AlertCircle size={18} />
              <p>Function-level overrides allow specific ML tasks to use different API keys. Leave empty to use the default provider for each organization.</p>
            </div>

            <div className="akm-functions-grid">
              {functionOverrides.map((fn) => (
                <div key={fn.functionId} className="akm-function-card">
                  <div className="akm-function-header">
                    <h3>{fn.functionName}</h3>
                    <span className={`akm-status-indicator ${fn.status}`} title={fn.status}></span>
                  </div>
                  <div className="akm-function-body">
                    <div className="akm-function-detail">
                      <label className="akm-label">API Key Provider</label>
                      <div className="akm-provider-selector">
                        <button className={`akm-provider-btn ${fn.provider === 'platform' ? 'active' : ''}`}>
                          Platform Default
                        </button>
                        <button className={`akm-provider-btn ${fn.provider === 'custom' ? 'active' : ''}`}>
                          Custom Key
                        </button>
                      </div>
                    </div>
                    {fn.provider === 'custom' && fn.keyFingerprint && (
                      <div className="akm-function-detail">
                        <label className="akm-label">Key</label>
                        <div className="akm-key-badge">
                          <code>{fn.keyFingerprint}</code>
                        </div>
                      </div>
                    )}
                  </div>
                  <div className="akm-function-footer">
                    <button className="akm-btn akm-btn-secondary akm-btn-xs">Configure</button>
                  </div>
                </div>
              ))}
            </div>

            {/* Function Assignment Rules */}
            <div className="akm-card" style={{ marginTop: '2rem' }}>
              <div className="akm-card-header">
                <h2>Assignment Rules</h2>
                <p className="akm-card-subtitle">Define which API key is used based on function and organization</p>
              </div>
              <div className="akm-card-body">
                <p className="akm-help-text" style={{ marginBottom: '1.5rem' }}>
                  Rules are evaluated top-to-bottom. First match wins. Leave empty to use organization&apos;s default.
                </p>
                <div className="akm-rules-list">
                  <div className="akm-rule-item">
                    <span className="akm-rule-num">1</span>
                    <span className="akm-rule-text">If function = <code>claude_classify</code> AND org = <code>Acme Corp</code> → use Acme&apos;s custom key</span>
                    <button className="akm-icon-btn"><Trash2 size={14} /></button>
                  </div>
                </div>
                <button className="akm-btn akm-btn-secondary" style={{ marginTop: '1rem' }}>
                  <Plus size={14} /> Add Rule
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default ApiKeyManagement;
