"""Generate charts for README and documentation."""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent.parent / "docs" / "assets"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# === Chart 1: Enterprise LLM API Spend Growth ===

def chart_llm_spend():
    """Enterprise LLM API spending growth (Menlo Ventures data)."""
    fig, ax = plt.subplots(figsize=(10, 5.5))

    years = ['2023\nH2', '2024\nH1', '2024\nH2', '2025\nH1', '2025\nH2', '2026\nH1\n(proj.)']
    spend = [1.8, 2.5, 3.5, 8.4, 12.5, 15.0]

    colors = ['#4A90D9', '#4A90D9', '#4A90D9', '#4A90D9', '#4A90D9', '#E8913A']

    bars = ax.bar(years, spend, color=colors, width=0.6, edgecolor='white', linewidth=0.5)

    # Add value labels
    for bar, val in zip(bars, spend):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.3,
                f'${val}B', ha='center', va='bottom', fontsize=11, fontweight='bold')

    # Growth annotations
    ax.annotate('', xy=(3, 9.5), xytext=(2, 4.5),
                arrowprops=dict(arrowstyle='->', color='#E63946', lw=2))
    ax.text(2.5, 6.5, '2.4x in\n6 months', ha='center', fontsize=9, color='#E63946', fontweight='bold')

    ax.set_ylabel('Enterprise LLM API Spend (USD Billions)', fontsize=11)
    ax.set_title('Enterprise LLM API Spending is Exploding', fontsize=14, fontweight='bold', pad=15)
    ax.set_ylim(0, 18)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.grid(axis='y', alpha=0.3)

    # Source
    ax.text(0.5, -0.15, 'Source: Menlo Ventures "State of Generative AI in the Enterprise" (2025)',
            transform=ax.transAxes, ha='center', fontsize=8, color='gray')

    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / 'llm_spend_growth.png', dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Saved: {OUTPUT_DIR / 'llm_spend_growth.png'}")


# === Chart 2: The Cost Governance Gap ===

def chart_cost_governance_gap():
    """Show the gap between spending growth and cost controls."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    # Left: Spending growth vs control adoption
    categories = ['LLM API\nSpend', 'Cost\nTracking', 'Budget\nLimits', 'Auto\nDegradation', 'Per-Agent\nBudgets']
    values = [100, 65, 20, 2, 1]
    colors = ['#E63946', '#4A90D9', '#4A90D9', '#2ECC71', '#2ECC71']

    bars = ax1.barh(categories, values, color=colors, height=0.5)
    for bar, val in zip(bars, values):
        label = f'{val}%' if val < 100 else 'Growing 3.2x/yr'
        ax1.text(bar.get_width() + 2, bar.get_y() + bar.get_height()/2,
                label, va='center', fontsize=10, fontweight='bold')

    ax1.set_xlim(0, 120)
    ax1.set_title('The Cost Governance Gap', fontsize=13, fontweight='bold')
    ax1.set_xlabel('Enterprise Adoption (%)', fontsize=10)
    ax1.spines['top'].set_visible(False)
    ax1.spines['right'].set_visible(False)

    # Legend
    from matplotlib.patches import Patch
    legend_elements = [
        Patch(facecolor='#E63946', label='Problem (growing)'),
        Patch(facecolor='#4A90D9', label='Existing tools'),
        Patch(facecolor='#2ECC71', label='Reins (new)'),
    ]
    ax1.legend(handles=legend_elements, loc='lower right', fontsize=9)

    # Right: Where agent costs come from
    labels = ['Retry\nLoops', 'Verbose\nCoT', 'Wrong\nModel', 'Context\nBloat', 'Other']
    sizes = [35, 25, 20, 15, 5]
    colors_pie = ['#E63946', '#FF6B6B', '#E8913A', '#F4C542', '#CCC']
    explode = (0.05, 0, 0, 0, 0)

    wedges, texts, autotexts = ax2.pie(sizes, labels=labels, autopct='%1.0f%%',
                                        colors=colors_pie, explode=explode,
                                        startangle=90, textprops={'fontsize': 10})
    for autotext in autotexts:
        autotext.set_fontweight('bold')
    ax2.set_title('Root Causes of Agent Cost Overruns', fontsize=13, fontweight='bold')

    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / 'cost_governance_gap.png', dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Saved: {OUTPUT_DIR / 'cost_governance_gap.png'}")


# === Chart 3: Agent Reliability Crisis ===

def chart_reliability():
    """Agent reliability statistics."""
    fig, ax = plt.subplots(figsize=(10, 5))

    categories = [
        'SWE-bench Verified\n(best agent)',
        'Real-world\nperformance',
        'Gartner: agentic AI\nprojects canceled by 2027',
        'Enterprise: reliability\nas #1 barrier',
    ]
    values = [79, 38, 40, 72]
    colors = ['#4A90D9', '#E63946', '#E63946', '#E8913A']

    bars = ax.barh(categories, values, color=colors, height=0.5)
    for bar, val in zip(bars, values):
        ax.text(bar.get_width() + 1.5, bar.get_y() + bar.get_height()/2,
                f'{val}%', va='center', fontsize=12, fontweight='bold')

    ax.set_xlim(0, 100)
    ax.set_title('The Agent Reliability Crisis', fontsize=14, fontweight='bold')
    ax.set_xlabel('Percentage (%)', fontsize=11)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.grid(axis='x', alpha=0.3)

    ax.text(0.5, -0.12, 'Sources: SWE-bench (2025), Gartner (2025), Pan et al. survey of 306 practitioners (2025)',
            transform=ax.transAxes, ha='center', fontsize=8, color='gray')

    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / 'reliability_crisis.png', dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Saved: {OUTPUT_DIR / 'reliability_crisis.png'}")


if __name__ == '__main__':
    chart_llm_spend()
    chart_cost_governance_gap()
    chart_reliability()
    print("All charts generated!")
