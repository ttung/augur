"""
Parse a BEAST MCC tree for further analysis in augur or
export for auspice (using `augur export`).
"""

from Bio import SeqIO, Phylo
import re, sys, json
import numpy as np
import datetime as dt
from treetime import TreeAnc
from .utils import write_json


def register_arguments(parser):
    """
    Arguments available to `augur import-beast` -- see `__init__.py`
    """
    parser.add_argument('--mcc', required=True, help="BEAST MCC tree")
    parser.add_argument('--time-units', default="years", type=str, help='not yet implemented. Default = years')
    parser.add_argument('--most-recent-tip-date-fmt', default="regex", choices=['regex','decimal'], required=True, help='method for finding the most recent tip date. Use "decimal" if the decimal date will be supplied or "regex" (default) if tip dates are encoded in tip names')
    parser.add_argument('--tip-date', help='decimal date of most recent tip or regular expression for date (if no value is given defaults to "[0-9]{4}(\-[0-9]{2})*(\-[0-9]{2})*$") if dates can be parsed out of tip names')
    parser.add_argument('--verbose', action="store_true")
    parser.add_argument('--recursion-limit', default=False, type=int, help="Set a custom recursion limit (dangerous!)")
    parser.add_argument('--output-tree', required=True, type=str, help='file name to write tree to')
    parser.add_argument('--output-node-data', required=True, type=str, help='file name to write (temporal) branch lengths as node data')



def parse_beast_tree(data, tipMap, verbose=False):
    """
    Parses the BEAST tree (and attributes etc) as encoded in NEXUS.

    Parameters
    ----------
    data : string
        The (really long) line in the NEXUS file beginning with "tree", pruned
        to start at the first "(" character. See 
    tipMap : dict
        Mapping of tips (as encoded in `data`) to their names
    verbose : bool, optional (default: false)
        Should output be printed?

    Returns
    -------
    <class 'Bio.Phylo.Newick.Clade'>

    Author: Gytis Dudas
    """

    i=0 ## is an adjustable index along the tree string, it is incremented to advance through the string
    stored_i=None ## store the i at the end of the loop, to make sure we haven't gotten stuck somewhere in an infinite loop

    cur_node = Phylo.Newick.Clade() ## new branch
    cur_node.name = 'root' ## start with root
    cur_node.clades = [] ## list of children
    node_count=0 ## node counter

    while i != len(data): ## while there's characters left in the tree string - loop away
        if stored_i == i and verbose==True:
            print('%d >%s<'%(i,data[i]))

        assert (stored_i != i),'\nTree string unparseable\nStopped at >>%s<<\nstring region looks like this: %s'%(data[i],data[i:i+5000]) ## make sure that you've actually parsed something last time, if not - there's something unexpected in the tree string
        stored_i=i ## store i for later

        if data[i] == '(': ## look for new nodes
            node = Phylo.Newick.Clade() ## new object
            node.name = 'NODE_%07d'%(node_count) ## node name
            if verbose==True:
                print('%d adding node %s'%(i, node.name))
            node.branch = 0.0 ## new node's branch length 0.0 for now
            node.up = cur_node ## new node's parent is current node
            node.clades = [] ## new node will have children
            node.attrs = {} ## initiate attrs dictionary
            cur_node.clades.append(node) ## add new node to children of current node
            cur_node = node ## new node is now current node
            node_count += 1 ## increment node counter
            i+=1 ## advance in tree string by one character

        numericalTip=re.match('(\(|,)([0-9]+)(\[|\:)',data[i-1:i+100]) ## look for tips in BEAST format (integers).
        if numericalTip is not None:
            node = Phylo.Newick.Clade() ## new object
            if tipMap:
                node.name = tipMap[numericalTip.group(2)] ## assign decoded name
            else:
                node.name = str(numericalTip.group(2)); ## use the integer as the name if tipMap isn't set
            if verbose==True:
                print('%d adding leaf (BEAST) %s (%s)'%(i,numericalTip.group(2), node.name))
            node.up = cur_node ## leaf's parent is cur_node
            node.attrs = {} ## initiate attrs dictionary
            cur_node.clades.append(node) ## assign leaf to children of parent
            cur_node = node ## cur_node is leaf

            i+=len(numericalTip.group(2)) ## advance in tree string by however many characters the tip is encoded

        alphaTip=re.match('(\(|,)(\'|\")*([A-Za-z\_\-\|\.0-9\?\/]+)(\'|\"|)(\[)*',data[i-1:i+200])  ## look for tips with unencoded names - if the tips have some unusual format you'll have to modify this
        if alphaTip is not None:
            if verbose==True:
                print('%d adding leaf (non-BEAST) %s'%(i,alphaTip.group(3)))
            node = Phylo.Newick.Clade() ## new object
            node.name = alphaTip.group(3) ## assign name
            node.up = cur_node ## leaf's parent is cur_node
            node.attrs = {} ## initiate attrs dictionary
            cur_node.clades.append(node) ## assign leaf to children of parent
            cur_node = node ## cur_node is leaf

            i+=len(alphaTip.group(3))+alphaTip.group().count("'")+alphaTip.group().count('"') ## advance in tree string by however many characters the tip is encoded

        multitypeNode=re.match('\)([0-9]+)\[',data[i-1:i+100]) ## look for multitype tree singletons.
        if multitypeNode is not None:
            if verbose==True:
                print('%d adding multitype node %s'%(i,multitypeNode.group(1)))
            i+=len(multitypeNode.group(1))

        commentBlock=re.match('(\:)*\[(&[A-Za-z\_\-{}\,0-9\.\%=\"\'\+!#]+)\]',data[i:])## look for MCC comments
        if commentBlock is not None:
            if verbose==True:
                print('%d comment: %s'%(i,commentBlock.group(2)))
            comment=commentBlock.group(2)
            numerics=re.findall('[,&][A-Za-z\_\.0-9]+=[0-9\-Ee\.]+',comment) ## find all entries that have values as floats
            strings=re.findall('[,&][A-Za-z\_\.0-9]+=["|\']*[A-Za-z\_0-9\.\+]+["|\']*',comment) ## strings
            treelist=re.findall('[,&][A-Za-z\_\.0-9]+={[A-Za-z\_,{}0-9\.]+}',comment) ## complete history logged robust counting (MCMC trees)
            sets=re.findall('[,&][A-Za-z\_\.0-9\%]+={[A-Za-z\.\-0-9eE,\"\_]+}',comment) ## sets and ranges
            figtree=re.findall('\![A-Za-z]+=[A-Za-z0-9#]+',comment) ## figtree comments, in case MCC was manipulated in FigTree

            for vals in strings: ## string states go here
                tr,val=vals.split('=') ## split into key and value
                tr=tr[1:] ## key has preceding & or ,
                if re.search('.*[^0-9\.eE].*',val) is not None: ## string regex can sometimes match floats (thanks to beast2), only allow values with at least one non-numeric character
                    if '+' in val: ## state was equiprobable with something else
                        equiprobable=val.split('+') ## get set of equiprobable states
                        val=equiprobable[np.random.randint(len(equiprobable))] ## DO NOT ALLOW EQUIPROBABLE DOUBLE ANNOTATIONS (which are in format "A+B")
                    cur_node.attrs[tr]=val.strip('"') ## assign value to attrs, strip "

            for vals in numerics: ## assign all parsed annotations to traits of current branch
                tr,val=vals.split('=') ## split each value by =, left side is name, right side is value
                tr=tr[1:] ## ignore preceding & or ,
                if 'prob' not in tr:
                    cur_node.attrs[tr]=float(val) ## assign float to attrs

            # for val in treelist:  ### enables parsing of complete history logger output from posterior trees
            #     tr,val=val.split('=')
            #     tr=tr[1:]
            #     completeHistoryLogger=re.findall('{([0-9]+,[0-9\.\-e]+,[A-Z]+,[A-Z]+)}',val)
            #     setattr(cur_node,'muts',[])
            #     for val in completeHistoryLogger:
            #         codon,timing,start,end=val.split(',')
            #         cur_node.muts.append('%s%s%s'%(start,codon,end))

            states={} ## credible sets will be stored here
            for vals in sorted(sets,key=lambda s:'.set.prob' in s.split('=')[0]): ## sort comments so sets come before set probabilities
                tr,val=vals.split('=') ## split comment into key and value
                tr=tr[1:] ## key has & or , in front

                if 'set' in tr: ## dealing with set
                    trait=tr.split('.set')[0] ## get trait name
                    if '.prob' not in tr: ## dealing with credible set
                        states[trait]=[v.strip('"') for v in val[1:-1].split(',')] ## store credible set
                    elif '.prob' in tr: ## dealing with probability set
                        probs=map(float,val[1:-1].split(',')) ## turn probability set into a list of floats
                        cur_node.attrs['%s_confidence'%(trait)]={t:p for t,p in zip(states[trait],probs)} ## create dictionary of state:probability

                elif 'range' in tr: ## range, best to ignore
                    pass
                    #cur_node.attrs[tr.replace('range','maxima')]=list(map(float,val[1:-1].split(','))) ## list of floats
                elif 'HPD' in tr: ## highest posterior densities
                    cur_node.attrs[tr.replace('95%_HPD','confidence')]=list(map(float,val[1:-1].split(','))) ## list of floats


            if len(figtree)>0:
                print('FigTree comment found, ignoring')

            i+=len(commentBlock.group()) ## advance in tree string by however many characters it took to encode comments

        nodeLabel=re.match('([A-Za-z\_\-0-9\.]+)(\:|\;)',data[i:])## look for old school node labels
        if nodeLabel is not None:
            if verbose==True:
                print('old school comment found: %s'%(nodeLabel.group(1)))
            cur_node.name=nodeLabel.group(1)
            i+=len(nodeLabel.group(1))

        branchLength=re.match('(\:)*([0-9\.\-Ee]+)',data[i:i+100]) ## look for branch lengths without comments
        if branchLength is not None:
            if verbose==True:
                print('adding branch length (%d) %.6f'%(i,float(branchLength.group(2))))
            setattr(cur_node,'branch_length',float(branchLength.group(2)))
            i+=len(branchLength.group()) ## advance in tree string by however many characters it took to encode branch length

        if data[i] == ',' or data[i] == ')': ## look for bifurcations or clade ends
            i+=1 ## advance in tree string
            cur_node = cur_node.up

        if data[i] == ';': ## look for string end
            return cur_node
            break ## end loop



def parse_nexus(tree_path, treestring_regex='tree [A-Za-z\_]+([0-9]+)', verbose=False):
    """
    Parses the BEAST MCC tree (NEXUS format)

    Parameters
    ----------
    tree_path : string or file handle open for reading
        The nexus tree file
    treestring_regex : string
        The regex to match the tree string in the nexus file (the really long
        string which typically starts with "tree" and looks similar to a newick tree)
    verbose : bool, optional (default: False)
        Should output be printed?

    Raises
    ------
    AssertionError
        If the tree was not correctly parsed

    Returns
    -------
    <class 'Bio.Phylo.BaseTree.Tree'>
        A tree with BEAST attrs set on each node (as applicable)

    Author: Gytis Dudas
    """

    tipFlag=False
    tips={}
    tipNum=0
    tree=None

    if isinstance(tree_path,str): ## determine if path or handle was provided to function
        handle=open(tree_path,'r')
    else:
        handle=tree_path

    for line in handle: ## iterate over lines
        l=line.strip('\n')

        nTaxa=re.search('dimensions ntax=([0-9]+);',l.lower()) ## get number of tips that should be in tree
        if nTaxa is not None:
            tipNum=int(nTaxa.group(1))
            if verbose:
                print('File should contain %d taxa'%(tipNum))

        treeString=re.search(treestring_regex,l) ## search for line with the tree
        if treeString is not None:
            treeString_start=l.index('(') ## find index of where tree string starts
            tree=parse_beast_tree(l[treeString_start:], tipMap=tips, verbose=verbose) ## parse tree string

            if verbose:
                print('Identified tree string')

        if tipFlag==True: ## going through tip encoding block
            tipEncoding=re.search('([0-9]+) ([A-Za-z\-\_\/\.\'0-9 \|?]+)',l) ## search for key:value pairs
            if tipEncoding is not None:
                tips[tipEncoding.group(1)]=tipEncoding.group(2).strip('"').strip("'") ## add to tips dict
                if verbose==True:
                    print('Identified tip translation %s: %s'%(tipEncoding.group(1),tips[tipEncoding.group(1)]))
            elif ';' not in l:
                print('tip not captured by regex:',l.replace('\t',''))

        if 'translate' in l.lower(): ## tip encoding starts on next line
            tipFlag=True
        if ';' in l:
            tipFlag=False

    assert tree,'Tree not captured by regex'
    assert tree.count_terminals()==tipNum,'Not all tips have been parsed.'
    print("Success parsing BEAST nexus")

    try:
        return Phylo.BaseTree.Tree.from_clade(tree)
    except RecursionError as err:
        print("FATAL ERROR")
        print("Recursion limit reached. You can try raising this with the `--recursion-limit` option")
        print("(Be careful with this). Your current limit is set to {}".format(sys.getrecursionlimit()))
        sys.exit(2)



def summarise_parsed_traits(tree):
    """
    Parameters
    ----------
    tree : <class 'Bio.Phylo.BaseTree.Tree'>
    """
    traits = {}
    for node in tree.find_clades():
        for attr in node.attrs:
            if attr not in traits:
                traits[attr] = [0, 0]
            if node.is_terminal():
                traits[attr][1] += 1
            else:
                traits[attr][0] += 1
    
    print("\nParsed BEAST traits:")
    print("{: <20}{: <12}{: <12}".format("name", "n(internal)", "n(terminal)"))
    for trait in traits:
        print("{: <20}{: <12}{: <12}".format(trait, traits[trait][0], traits[trait][1]))
    print("\n")



def fake_alignment(T):
    """
    Fake alignment to appease treetime when only using it for naming nodes...
    This is lifted from refine.py and ideally could be imported

    Parameters
    -------
    T : <class 'Bio.Phylo.BaseTree.Tree'>

    Returns
    -------
    <class 'Bio.Align.MultipleSeqAlignment'>
    """
    from Bio import SeqRecord, Seq, Align
    seqs = []
    for n in T.get_terminals():
        seqs.append(SeqRecord.SeqRecord(seq=Seq.Seq('ACGT'), id=n.name, name=n.name, description=''))
    aln = Align.MultipleSeqAlignment(seqs)
    return aln



def get_root_date_offset(tree):
    """
    years from most recent tip of the root
    """
    greatest_dist2root = 0
    for leaf in tree.get_terminals():
        if leaf.dist2root > greatest_dist2root:
            greatest_dist2root = leaf.dist2root
    return greatest_dist2root



def find_most_recent_tip(tree, regex="[0-9]{4}(\-[0-9]{2})*(\-[0-9]{2})*$", date_fmt="%Y-%m-%d", dateDelimiter='-'):
    """
    Find the most recent tip in the tree

    Parameters
    --------
    tree : <class 'Bio.Phylo.BaseTree.Tree'>
    regex : string
        The regex used to extract the date (e.g. isolate collection date
        from each tip in the string.
        default: hyphen delimited numbers at the end of tip name
    date_fmt : string
        The format of the extracted date. 
        (default: "%Y-%m-%d", e.g. "2012-10-30")
    dateDelimeter : string
        The delimeter in `date_fmt`

    Raises
    ------
    AssertionError
        If any tips were not matched by the regex

    Returns
    -------
    float
        The date of the most recent tip in the tree in decimal format

    See also: `decimalDate()`
    Author: Gytis Dudas
    """

    def decimalDate(date, date_fmt="%Y-%m-%d", variable=False, dateDelimiter='-'):
        """ Converts calendar dates in specified format to decimal date. """
        if variable==True: ## if date is variable - extract what is available
            dateL=len(date.split(dateDelimiter))
            if dateL==2:
                date_fmt=dateDelimiter.join(date_fmt.split(dateDelimiter)[:-1])
            elif dateL==1:
                date_fmt=dateDelimiter.join(date_fmt.split(dateDelimiter)[:-2])

        adatetime=dt.datetime.strptime(date,date_fmt) ## convert to datetime object
        year = adatetime.year ## get year
        boy = dt.datetime(year, 1, 1) ## get beginning of the year
        eoy = dt.datetime(year + 1, 1, 1) ## get beginning of next year
        return year + ((adatetime - boy).total_seconds() / ((eoy - boy).total_seconds())) ## return fractional year

    leaf_names=[leaf.name for leaf in tree.get_terminals()] ## get names of tips
    date_regex=re.compile(regex) ## regex pattern
    regex_matches=[date_regex.search(leaf) for leaf in leaf_names] ## search tips with regex
    assert regex_matches.count(None)==0,'These tip dates were not captured by regex %s: %s'%(regex,', '.join([leaf for leaf in leaf_names if date_regex.search(leaf)==None])) ## number of tips should match number of regex matches
    decimal_dates=[decimalDate(date_regex.search(leaf).group(),date_fmt=date_fmt,variable=True,dateDelimiter=dateDelimiter) for leaf in leaf_names] ## convert tip calendar dates to decimal dates

    return max(decimal_dates) ## return highest tip date



def calc_tree_dates(tree, time_units, tip_date, most_recent_tip_date_fmt):
    """
    Extract date information from the tree

    Parameters
    --------
    tree : <class 'Bio.Phylo.BaseTree.Tree'>
    time_units : string
    tip_date : null | string
    most_recent_tip_data_fmt : string {"regex" | "decimal"}

    Returns
    --------
    tuple
        [0] : float
            The root date offset
        [1] : float
            The date of the most recent tip in the tree
    """

    # time units need to be adjusted by the most recent tip date
    root_date_offset = get_root_date_offset(tree)
    print("Root date offset:", root_date_offset, time_units)

    print(most_recent_tip_date_fmt)
    if most_recent_tip_date_fmt=='regex':
        if tip_date:
            most_recent_tip = find_most_recent_tip(tree, regex=tip_date)
        else:
            most_recent_tip = find_most_recent_tip(tree)
    elif most_recent_tip_date_fmt=='decimal':
        most_recent_tip = float(tip_date)

    return (root_date_offset, most_recent_tip)



def collect_node_data(tree, root_date_offset, most_recent_tip_date):
    """
    Collect & summarise the BEAST traits included on the tree in a format
    applicable for augur to use (i.e. the "node_data.json" file).

    Parameters
    --------
    tree : <class 'Bio.Phylo.BaseTree.Tree'>
    root_date_offset : float
    most_recent_tip_date : float

    Returns
    --------
    dict
        the keys are dependent on the content of the BEAST input
    """

    # Example of a typical tree time export which we need to emulate:
    # "branch_length": 0.0032664876882838745,
    # "numdate": 2015.3901042843218,
    # "clock_length": 0.0032664876882838745,
    # "mutation_length": 0.003451507603103053,
    # "date": "2015-05-23",
    # "num_date_confidence": [2015.032, 2015.6520]

    def exclude_trait(name):
        if 'length' in name or 'height' in name:
            return True
        return False


    node_data = {}
    root_date = most_recent_tip_date - root_date_offset
    for n in tree.find_clades():
        node_data[n.name] = {attr: n.attrs[attr] for attr in n.attrs if not exclude_trait(attr)} ## add all "valid" beast tree traits
        node_data[n.name]['num_date'] = root_date + n.dist2root ## num_date is decimal date of node
        node_data[n.name]['clock_length'] = n.branch_length ## assign BEAST branch length as regular branch length
        if n.is_terminal():
            node_data[n.name]['posterior'] = 1.0 ## assign posterior of 1.0 to every tip (for aesthetics)
        else:
            node_data[n.name]['num_date_confidence'] = [most_recent_tip_date - height for height in n.attrs['height_confidence']] ## convert beast 95% HPDs into decimal date confidences

    return node_data



def compute_entropies_for_discrete_traits(tree):
    """
    Computes entropies for discrete traits.
    Discrete traits are assumed to be those where the value is
    a dictionary.
    This will set a "entropy" value for each identified discrete trait
    on all applicable nodes in the tree.

    Properties
    -------
    tree : <class 'Bio.Phylo.BaseTree.Tree'>
        BEAST traits are set as key-value pairs on node.attrs

    Author: James Hadfield
    """
    alphabets={} ## store alphabets
    for clade in tree.find_clades(): ## iterate over branches
        for attr in [key for key in clade.attrs if isinstance(clade.attrs[key], dict)]: ## iterate over branch attributes
            if attr in alphabets: ## if attr seen before
                for val in clade.attrs[attr]: ## iterate over attribute values of the node
                    if val not in alphabets[attr]: ## not seen this attribute value before
                        alphabets[attr].append(val)
            else:
                alphabets[attr]=[] ## not seen trait before - start a list of its values
                for val in clade.attrs[attr]: ## iterate over trait values for this branch
                    alphabets[attr].append(val)

    for clade in tree.find_clades(): ## iterate over branches
        for trait in alphabets: ## iterate over traits
            if trait in clade.attrs: ## branch has trait (in case there's a leaf-node difference in trait presence)
                trait_name=trait.split('_')[0] ## extract trait name root
                pdis=np.array([clade.attrs[trait][state] if state in clade.attrs[trait] else 0.0 for state in alphabets[trait]]) ## create state profile
                clade.attrs['%s_entropy'%(trait_name)] = -np.sum(pdis*np.log(pdis+1e-10)) ## compute entropy for trait



def print_what_to_do_next(nodes, mcc_path, tree_path, node_data_path):
    """
    Print a suggested `auspice_config.json` file, which the user will have to configure
    and provide to `augur export`. There is not enough information in a MCC tree to do
    this automatically.
    """

    def include_key(k):
        exclude_list = ["clock_length"]
        return (not k.endswith("_confidence") and not k.endswith("_entropy") and k not in exclude_list)
    attrs = set()
    for node in nodes:
        attrs.update({k for k in nodes[node].keys() if include_key(k)})

    def make_color_block(attr):
        if attr == "num_date":
            menuItem = "Sampling Date"
        else:
            menuItem = attr
        return {"menuItem": menuItem, "legendTitle": menuItem, "type": "continuous"}

    auspice_config = {
        "title": "Title for auspice to display",
        "color_options": {attr: make_color_block(attr) for attr in attrs},
        "defaults": {"colorBy": "num_date"},
        "maintainer": ["author names (displayed in footer)", "author URL"],
        "panels": ["tree"],
        # "updated": "date (displayed in footer)",
    }


    print("\n---------------------------------------------------------")
    print("Successfully parsed BEAST MCC tree {}".format(mcc_path))
    print("Files produced:\n\t{}\n\t{}".format(tree_path, node_data_path))
    print("\n")
    print("For `augur export` you will need to provide a `auspice_config.json` file, which we cannot automatically generate. This file is typically placed in `config/auspice_config.json`. Here is a template:\n")
    print(json.dumps(auspice_config, indent=4))
    print("\nYou can continue further analysis using augur, or export JSONs for auspice.")
    print("Here is an example of the command to export the data without further analysis (see `augur export -h` for more options)")
    print("\n`augur export --tree {tree} --node-data {nd} --auspice-config config/auspice_config.json --output-tree auspice/<dataset_name>_tree.json --output-meta auspice/<dataset_name>_meta.json`".format(tree=tree_path, nd=node_data_path))
    print("---------------------------------------------------------")



def run(args):
    '''
    BEAST MCC tree to newick and node-data JSON for further augur processing / export
    '''
    verbose = args.verbose
    print("importing from BEAST MCC tree", args.mcc)

    if args.recursion_limit:
        print("Setting recursion limit to %d"%(args.recursion_limit))
        sys.setrecursionlimit(args.recursion_limit)

    # node data is the dict that will be exported as json
    node_data = {
        'comment': "Imported from a BEAST MCC tree using `augur import-beast`",
        'mcc_file': args.mcc
    }

    tree = parse_nexus(tree_path=args.mcc, verbose=args.verbose)
    summarise_parsed_traits(tree)
    # Phylo.draw_ascii(tree)
    # instantiate treetime for the sole reason to name internal nodes (!)
    # note that tt.tree = tree, and this is modified in-place by this function
    tt = TreeAnc(tree=tree, aln=fake_alignment(tree), ref=None, gtr='JC69', verbose=1)


    # extract date information from the tree
    root_date_offset, most_recent_tip = calc_tree_dates(tree, args.time_units, args.tip_date, args.most_recent_tip_date_fmt)
    compute_entropies_for_discrete_traits(tree)
    
    node_data['nodes'] = collect_node_data(tree, root_date_offset, most_recent_tip)

    tree_success = Phylo.write(tree, args.output_tree, 'newick', format_branch_length='%1.8f')
    json_success = write_json(node_data, args.output_node_data)

    print_what_to_do_next(nodes=node_data['nodes'], mcc_path=args.mcc, tree_path=args.output_tree, node_data_path=args.output_node_data)

    return 0 if (tree_success and json_success) else 1
